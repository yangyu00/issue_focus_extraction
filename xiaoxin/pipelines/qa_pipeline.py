"""流水线二：QA 知识生成（原 generate_qa.py 的业务部分）。

基于 xxa_bomc_qa_non_vendor 的解析结果：按时间取数 → 过滤 → 按 symptom 分组 →
查 GBase 补工单详情 → 直连大模型聚类生成标准问答对 → 写入 xxa_city_qa_knowledge，
并导出 GBase 明细 / QA Excel。

所有数据库读写来自 xiaoxin.db；聚类提示词由 prompts/qa_cluster.txt 维护。
"""
import ast
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import json_repair
import pandas as pd

from xiaoxin.config import (
    DATA_DIR,
    GENERATE_QA_ERROR_LOG_NAME,
    GENERATE_QA_INFO_LOG_NAME,
    QA_LLM_MODEL,
)
from xiaoxin.db import (
    NON_VENDOR_TABLE,
    get_records_by_ids,
    query_gbase_by_task_oids,
    read_non_vendor,
    save_qa_knowledge,
)
from xiaoxin.llm.client import chat, extract_json_block
from xiaoxin.llm.prompt_loader import load_prompt
from xiaoxin.logging_setup import logger, setup_logging

setup_logging(GENERATE_QA_INFO_LOG_NAME, GENERATE_QA_ERROR_LOG_NAME)

CHUNK_SIZE = 400  # 单个 symptom 每批送模型的记录数


# ================= 辅助命令：Excel 反查 =================
def parse_matched_issue_ids(value) -> list:
    """解析 matched_issue_ids：支持 list 字面量字符串、逗号分隔字符串、已是 list。"""
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if not isinstance(value, str) or not value.strip():
        return []
    value = value.strip()
    try:
        parsed = ast.literal_eval(value)
        if isinstance(parsed, list):
            return [str(v).strip() for v in parsed if str(v).strip()]
    except (ValueError, SyntaxError):
        pass
    return [v.strip() for v in value.split(",") if v.strip()]


def enrich_qa_excel(input_xlsx: str):
    """读 QA Excel 的 matched_issue_ids → 反查 task_oid → 查 GBase → 另存明细 Excel。

    由 --enrich-excel 调用的手动辅助命令。
    """
    if not os.path.exists(input_xlsx):
        logger.error(f"文件不存在: {input_xlsx}")
        return
    df_input = pd.read_excel(input_xlsx)
    if "matched_issue_ids" not in df_input.columns:
        logger.error(f"文件中缺少 matched_issue_ids 列，现有列：{df_input.columns.tolist()}")
        return

    all_ids = set()
    for val in df_input["matched_issue_ids"]:
        all_ids.update(parse_matched_issue_ids(val))
    id_list = [int(i) for i in all_ids if str(i).strip().isdigit()]
    logger.info(f"共解析出 {len(id_list)} 个唯一 id，查询 {NON_VENDOR_TABLE} 获取 task_oid...")

    non_vendor_df = get_records_by_ids(id_list)
    if non_vendor_df.empty or "task_oid" not in non_vendor_df.columns:
        logger.error("未查到对应记录或缺少 task_oid 列，终止。")
        return

    id_to_task_oid = {
        str(row["id"]): str(row["task_oid"])
        for _, row in non_vendor_df.iterrows()
        if pd.notna(row.get("task_oid")) and pd.notna(row.get("id"))
    }
    all_task_oids = list(set(id_to_task_oid.values()))
    gbase_records = query_gbase_by_task_oids(all_task_oids)
    gbase_map = {str(r.get("TASK_OID", "")): r for r in gbase_records}

    output_rows = []
    for _, row in df_input.iterrows():
        for id_val in parse_matched_issue_ids(row.get("matched_issue_ids", "")):
            task_oid = id_to_task_oid.get(str(id_val), "")
            info = gbase_map.get(str(task_oid), {}) if task_oid else {}
            output_rows.append({
                "task_oid": task_oid,
                "报障标题": info.get("TASK_TITLE", ""),
                "报障内容": info.get("fld_faultcontent", ""),
                "分公司处理意见": info.get("fld_column_2", ""),
            })
    if not output_rows:
        logger.warning("未生成任何输出行，请检查 matched_issue_ids 列内容。")
        return

    base, ext = os.path.splitext(input_xlsx)
    output_dir = os.path.dirname(input_xlsx) or "."
    os.makedirs(output_dir, exist_ok=True)
    output_xlsx = os.path.join(output_dir, f"{os.path.basename(base)}_gbase_result{ext}")
    pd.DataFrame(output_rows).to_excel(output_xlsx, index=False)
    logger.info(f"✅ 完成！共输出 {len(output_rows)} 行，已保存到：{output_xlsx}")
    print(f"✅ 完成！共输出 {len(output_rows)} 行，已保存到：{output_xlsx}")


# ================= 数据清洗与模型调用 =================
def convert_actions(json_str) -> str:
    """把 branch_actions 的 JSON 数组摊平成「操作 :: 结果 :: 建议」。"""
    if pd.isna(json_str) or not isinstance(json_str, str):
        return ""
    try:
        data_list = json.loads(json_str)
        parts = []
        for item in data_list:
            parts.extend([
                str(item.get("operation", "")),
                str(item.get("result", "")),
                str(item.get("suggestion", "")),
            ])
        return " :: ".join(parts)
    except (json.JSONDecodeError, TypeError, AttributeError):
        return ""


def call_qa_model(model_input_str: str) -> str:
    """直连大模型生成 QA 聚类，返回可解析的 JSON 字符串。"""
    prompt = load_prompt("qa_cluster", input=model_input_str)
    raw = chat(prompt, model=QA_LLM_MODEL)
    return extract_json_block(raw)


def process_group(symptom, group, gbase_result_collector: dict) -> list:
    """处理单个 symptom 组：查 GBase + 分批调用模型聚类。"""
    qa_list = []
    total_records = len(group)

    # 查询 GBase 明细
    try:
        task_oid_list = group["task_oid"].dropna().astype(str).unique().tolist()
        logger.info(f"symptom='{symptom}'：向 GBase 查询 {len(task_oid_list)} 个 task_oid")
        gbase_records = query_gbase_by_task_oids(task_oid_list)
        gbase_rows = [{
            "task_oid": r.get("TASK_OID", ""),
            "报障标题": r.get("TASK_TITLE", ""),
            "报障内容": r.get("fld_faultcontent", ""),
            "分公司处理意见": r.get("fld_column_2", ""),
        } for r in gbase_records]
        gbase_result_collector[symptom] = gbase_rows
        logger.info(f"symptom='{symptom}'：GBase 返回 {len(gbase_rows)} 条记录")
    except Exception as e:
        logger.error(f"symptom='{symptom}' GBase 查询失败: {e}", exc_info=True)
        gbase_result_collector[symptom] = []

    # 分批聚类
    try:
        for start_idx in range(0, total_records, CHUNK_SIZE):
            chunk = group.iloc[start_idx:start_idx + CHUNK_SIZE]
            batch_num = (start_idx // CHUNK_SIZE) + 1
            lines = [
                f"{row['task_oid']}: {row['symptom']} :: {row['symptom_detail']} :: "
                f"{row['cause']} :: {row['formatted_actions']}"
                for _, row in chunk.iterrows()
            ]
            model_input_str = "\n".join(lines)
            logger.info(
                f"提交 symptom='{symptom}' 批次 {batch_num}，{len(chunk)} 条 (总 {total_records} 条)"
            )
            result = ""
            try:
                result = call_qa_model(model_input_str)
                result_json = json_repair.loads(result)
                for cluster in (result_json or {}).get("clusters", []):
                    qa_list.append({
                        "symptom": symptom,
                        "standard_question": cluster.get("standard_question", ""),
                        "standard_answer": cluster.get("standard_answer", ""),
                        "matched_issue_ids": str(cluster.get("matched_issue_ids", [])),
                    })
            except Exception as e:
                logger.error(
                    f"调用/解析模型失败 symptom='{symptom}' 批次 {batch_num}: {e} | 原始内容: {result[:200]}...",
                    exc_info=True,
                )
    except Exception as e:
        logger.error(f"处理 symptom='{symptom}' 时发生错误: {e}", exc_info=True)

    return qa_list


def process_non_vendor_data(start_time: str, end_time: str, save_qa_excel: bool = False):
    """读取时间范围内的非厂商工单 → 过滤 → 分组聚类 → 写库 / 导出 Excel。"""
    try:
        logger.info(f"开始读取表: {NON_VENDOR_TABLE}, 时间范围: {start_time} 至 {end_time}")
        df = read_non_vendor(start_time, end_time)
        if df.empty:
            logger.warning(f"时间段 {start_time} ~ {end_time} 内无数据。")
            return

        initial_count = len(df)
        logger.info(f"读取到总记录数: {initial_count}")

        df_filtered = df[df["symptom_detail"] != "其他"].copy()
        logger.info(f"过滤 symptom_detail='其他' 后剩余: {len(df_filtered)}")
        df_filtered = df_filtered[
            df_filtered["branch_actions"] != '[{"operation":"无","result":"无","suggestion":"无"}]'
        ].copy()
        logger.info(f"过滤 branch_actions 特定值后剩余: {len(df_filtered)}，共过滤掉 {initial_count - len(df_filtered)} 条")

        if df_filtered.empty:
            logger.warning("过滤后无剩余数据，无需调用模型。")
            return

        required_cols = ["task_oid", "symptom", "symptom_detail", "cause", "branch_actions"]
        missing = [c for c in required_cols if c not in df_filtered.columns]
        if missing:
            logger.error(f"缺少必要列: {missing}，现有列: {list(df_filtered.columns)}")
            return

        df_filtered["formatted_actions"] = df_filtered["branch_actions"].apply(convert_actions)
        grouped = df_filtered.groupby("symptom")
        logger.info("=== 各 Symptom 记录数统计 ===\n" + df_filtered["symptom"].value_counts().to_string())

        all_qa_data = []
        gbase_result_collector = {}
        with ThreadPoolExecutor(max_workers=1) as executor:
            future_to_symptom = {
                executor.submit(process_group, symptom, group, gbase_result_collector): symptom
                for symptom, group in grouped
            }
            for future in as_completed(future_to_symptom):
                symptom = future_to_symptom[future]
                try:
                    qa_list = future.result()
                    if qa_list:
                        all_qa_data.extend(qa_list)
                        logger.info(f"成功获取 symptom='{symptom}' 的 {len(qa_list)} 条 QA 结果")
                except Exception as e:
                    logger.error(f"任务执行异常 symptom='{symptom}': {e}", exc_info=True)

        # GBase 明细 Excel（所有 symptom 合并到一个 sheet）
        all_gbase_rows = []
        for sym, rows in gbase_result_collector.items():
            for row in rows:
                row["故障类型"] = sym
                all_gbase_rows.append(row)
        os.makedirs(DATA_DIR, exist_ok=True)
        if all_gbase_rows:
            gbase_output_file = os.path.join(DATA_DIR, f"gbase_{start_time}_{end_time}.xlsx")
            pd.DataFrame(
                all_gbase_rows,
                columns=["故障类型", "task_oid", "报障标题", "报障内容", "分公司处理意见"],
            ).to_excel(gbase_output_file, index=False)
            logger.info(f"GBase 查询结果已保存到 {gbase_output_file}")
            print(f"GBase 查询结果已保存到 {gbase_output_file}")
        else:
            logger.warning("未获取到任何 GBase 数据，未生成 gbase Excel 文件。")

        # 可选：QA 结果 Excel
        if save_qa_excel and all_qa_data:
            qa_output_file = os.path.join(DATA_DIR, f"qa_{start_time}_{end_time}.xlsx")
            pd.DataFrame(all_qa_data).to_excel(qa_output_file, index=False)
            logger.info(f"成功保存 {len(all_qa_data)} 条 QA 对到 {qa_output_file}")

        # 全部 symptom 组处理完成后统一写库一次
        if all_qa_data:
            save_qa_knowledge(all_qa_data)
        else:
            logger.warning("未提取到任何有效 QA 数据。")
    except Exception as e:
        logger.error(f"处理数据时出错: {e}", exc_info=True)
