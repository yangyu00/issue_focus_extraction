"""流水线二：QA 知识生成。

基于 xxa_bomc_qa_non_vendor 的解析结果：按时间取数 → 过滤 → 按 symptom 分组 →
查 GBase 补工单详情 → 直连大模型聚类生成标准问答对 → 写入 xxa_city_qa_knowledge，
并导出 GBase 明细 / QA Excel。

所有数据库读写来自 xiaoxin_work_order.db；聚类提示词由 prompts/qa_cluster.txt 维护。
``--enrich-excel`` 的手动反查命令已拆到 xiaoxin_work_order/cli/qa_excel.py。
日志由 CLI 入口统一配置。
"""
import json
import os
from dataclasses import dataclass

import json_repair
import pandas as pd

from xiaoxin_work_order.common import format_issue_ids
from xiaoxin_work_order.config import DATA_DIR, QA_LLM_MODEL
from xiaoxin_work_order.db import (
    NON_VENDOR_TABLE,
    query_gbase_by_task_oids,
    read_non_vendor,
    save_qa_knowledge,
)
from xiaoxin_work_order.llm.client import chat, extract_json_block
from xiaoxin_work_order.llm.prompt_loader import load_prompt
from xiaoxin_work_order.logging_setup import logger

CHUNK_SIZE = 400  # 单个 symptom 每批送模型的记录数

# 模型在「分公司没有任何操作」时固定输出的 JSON 字面量（见 prompts/branch_actions.txt）。
# 提为具名常量，避免过滤条件里散落一整串与模型格式逐字绑定的字符串。
EMPTY_BRANCH_ACTIONS = '[{"operation":"无","result":"无","suggestion":"无"}]'


@dataclass
class GroupResult:
    """单个 symptom 组的产出：聚类结果 + 该组查到的 GBase 明细。"""

    qa_items: list
    gbase_rows: list


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


def process_group(symptom, group) -> GroupResult:
    """处理单个 symptom 组：查 GBase + 分批调用模型聚类。

    结果一次性返回，不再通过参数里的「collector」顺带往外写状态。
    """
    qa_list = []
    total_records = len(group)
    gbase_rows = []

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
        logger.info(f"symptom='{symptom}'：GBase 返回 {len(gbase_rows)} 条记录")
    except Exception as e:
        logger.error(f"symptom='{symptom}' GBase 查询失败: {e}", exc_info=True)

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
                        "matched_issue_ids": format_issue_ids(cluster.get("matched_issue_ids", [])),
                    })
            except Exception as e:
                logger.error(
                    f"调用/解析模型失败 symptom='{symptom}' 批次 {batch_num}: {e} | 原始内容: {result[:200]}...",
                    exc_info=True,
                )
    except Exception as e:
        logger.error(f"处理 symptom='{symptom}' 时发生错误: {e}", exc_info=True)

    return GroupResult(qa_items=qa_list, gbase_rows=gbase_rows)


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
        df_filtered = df_filtered[df_filtered["branch_actions"] != EMPTY_BRANCH_ACTIONS].copy()
        logger.info(
            f"过滤 branch_actions 全「无」记录后剩余: {len(df_filtered)}，"
            f"共过滤掉 {initial_count - len(df_filtered)} 条"
        )

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

        # 组之间本就串行（限流由 llm.client 统一控制），直接用 for，不再包一层
        # max_workers=1 的线程池 + future_to_symptom 映射。
        all_qa_data = []
        all_gbase_rows = []
        for symptom, group in grouped:
            try:
                result = process_group(symptom, group)
            except Exception as e:
                logger.error(f"任务执行异常 symptom='{symptom}': {e}", exc_info=True)
                continue
            if result.qa_items:
                all_qa_data.extend(result.qa_items)
                logger.info(f"成功获取 symptom='{symptom}' 的 {len(result.qa_items)} 条 QA 结果")
            for row in result.gbase_rows:
                all_gbase_rows.append({"故障类型": symptom, **row})

        # GBase 明细 Excel（所有 symptom 合并到一个 sheet）
        os.makedirs(DATA_DIR, exist_ok=True)
        if all_gbase_rows:
            gbase_output_file = os.path.join(DATA_DIR, f"gbase_{start_time}_{end_time}.xlsx")
            pd.DataFrame(
                all_gbase_rows,
                columns=["故障类型", "task_oid", "报障标题", "报障内容", "分公司处理意见"],
            ).to_excel(gbase_output_file, index=False)
            logger.info(f"GBase 查询结果已保存到 {gbase_output_file}")
        else:
            logger.warning("未获取到任何 GBase 数据，未生成 gbase Excel 文件。")

        # 可选：QA 结果 Excel
        if save_qa_excel and all_qa_data:
            qa_output_file = os.path.join(DATA_DIR, f"qa_{start_time}_{end_time}.xlsx")
            pd.DataFrame(all_qa_data).to_excel(qa_output_file, index=False)
            logger.info(f"成功保存 {len(all_qa_data)} 条 QA 对到 {qa_output_file}")

        # 全部 symptom 组处理完成后统一写库一次
        if all_qa_data:
            written = save_qa_knowledge(all_qa_data)
            logger.info(f"写入标准问答对 {written} 条")
        else:
            logger.warning("未提取到任何有效 QA 数据。")
    except Exception as e:
        logger.error(f"处理数据时出错: {e}", exc_info=True)
