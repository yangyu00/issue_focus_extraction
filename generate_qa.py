#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QA 知识生成（单文件自包含）。

基于 xxa_bomc_qa_non_vendor 的解析结果：按时间取数 → 过滤 → 按 symptom 分组 →
查 GBase 补工单详情 → 直连大模型聚类生成标准问答对 → 写入 xxa_city_qa_knowledge，
并导出 GBase 明细 Excel。

本次重构把原 gbase_excel_query.py / sql_query.py 的业务全部内联到本文件，
不再需要额外的 GBase 查询模块；QA 聚类提示词改由 prompts/qa_cluster.txt 维护。
"""
import argparse
import ast
import datetime
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

import json_repair
import pandas as pd
import pymysql
from sqlalchemy import create_engine, text

from config import (
    DATA_DIR,
    GBASE_CONFIG,
    GENERATE_QA_ERROR_LOG_NAME,
    GENERATE_QA_INFO_LOG_NAME,
    QA_LLM_MODEL,
    QA_MYSQL_CONFIG,
    mysql_url,
)
from llm_client import chat, extract_json_block
from prompt_loader import load_prompt
from utils import logger, setup_logging

setup_logging(GENERATE_QA_INFO_LOG_NAME, GENERATE_QA_ERROR_LOG_NAME)

engine = create_engine(mysql_url(QA_MYSQL_CONFIG), pool_recycle=3600, pool_pre_ping=True)

NON_VENDOR_TABLE = "xxa_bomc_qa_non_vendor"
QA_TABLE = "xxa_city_qa_knowledge"
CHUNK_SIZE = 400


# ================= GBase（opera 库）查询：原 gbase_excel_query.py =================
def query_gbase_by_task_oids(task_oid_list: list) -> list:
    """按 TASK_OID 列表查询 GBase opera.xiaoxin 的工单原始字段。"""
    if not task_oid_list:
        logger.info("task_oid 列表为空，跳过 GBase 查询。")
        return []
    placeholders = ",".join(["%s"] * len(task_oid_list))
    query = f"""
        SELECT TASK_OID, TASK_TITLE, fld_faultcontent, fld_column_2
        FROM xiaoxin
        WHERE TASK_OID IN ({placeholders})
    """
    try:
        conn = pymysql.connect(**GBASE_CONFIG)
        try:
            with conn.cursor() as cursor:
                cursor.execute(query, tuple(task_oid_list))
                columns = [desc[0] for desc in cursor.description]
                results = cursor.fetchall()
        finally:
            conn.close()
        if not results:
            logger.warning(f"GBase 中未查询到 {len(task_oid_list)} 个 task_oid 对应的记录。")
            return []
        return [dict(zip(columns, row)) for row in results]
    except Exception as e:
        logger.error(f"GBase 数据库连接或查询失败: {e}")
        return []


# ================= MySQL 按 id 查记录：原 sql_query.py =================
def get_records_by_ids(id_list) -> pd.DataFrame:
    """根据 id 列表查询 xxa_bomc_qa_non_vendor（参数化，兼容任意长度）。"""
    if not id_list:
        return pd.DataFrame()
    # SQLAlchemy text() 用命名占位符，不能沿用 pymysql 的 %s
    params = {f"id_{i}": value for i, value in enumerate(id_list)}
    placeholders = ",".join(f":{key}" for key in params)
    sql = text(f"SELECT * FROM {NON_VENDOR_TABLE} WHERE id IN ({placeholders})")
    try:
        return pd.read_sql(sql, engine, params=params)
    except Exception as e:
        logger.error(f"按 id 查询 {NON_VENDOR_TABLE} 失败: {e}")
        return pd.DataFrame()


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

    对应原 gbase_excel_query.py 的 main()，作为手动辅助命令保留。
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


def save_to_database(qa_data: list):
    """把生成的 QA 写入 xxa_city_qa_knowledge。"""
    if not qa_data:
        return
    fixed_values = {
        "knowledge_source": "工单提取",
        "work_id": "system",
        "name": "知识管理平台",
        "city": "999",
        "city_text": "全省",
    }
    sql = f"""
        INSERT INTO {QA_TABLE}
        (question, answer, knowledge_source, work_id, name, city, city_text, task_oids)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """
    connection = pymysql.connect(**QA_MYSQL_CONFIG)
    try:
        with connection.cursor() as cursor:
            data_to_insert = [(
                item["standard_question"],
                item["standard_answer"],
                fixed_values["knowledge_source"],
                fixed_values["work_id"],
                fixed_values["name"],
                fixed_values["city"],
                fixed_values["city_text"],
                str(item.get("matched_issue_ids", ""))
                .lstrip("[").rstrip("]").replace(" ", ""),
            ) for item in qa_data]
            cursor.executemany(sql, data_to_insert)
        connection.commit()
        print(f"成功插入 {len(data_to_insert)} 条数据到 {QA_TABLE} 表。")
    except Exception as e:
        logger.error(f"数据库操作出错: {e}")
        connection.rollback()
    finally:
        connection.close()


def process_non_vendor_data(start_time: str, end_time: str, save_qa_excel: bool = False):
    """读取时间范围内的非厂商工单 → 过滤 → 分组聚类 → 写库 / 导出 Excel。"""
    try:
        start_dt = f"{start_time} 00:00:00"
        end_dt = f"{end_time} 23:59:59"
        logger.info(f"开始读取表: {NON_VENDOR_TABLE}, 时间范围: {start_time} 至 {end_time}")

        sql = text(
            f"SELECT * FROM {NON_VENDOR_TABLE} "
            f"WHERE last_time >= :start_dt AND last_time <= :end_dt"
        )
        df = pd.read_sql(sql, engine, params={"start_dt": start_dt, "end_dt": end_dt})
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

        # 统一写库一次（修复原来「循环内写一次 + 结尾再写一次」造成的重复入库）
        if all_qa_data:
            save_to_database(all_qa_data)
        else:
            logger.warning("未提取到任何有效 QA 数据。")
    except Exception as e:
        logger.error(f"处理数据时出错: {e}", exc_info=True)


# ================= 命令行入口 =================
def _valid_date(value):
    try:
        datetime.datetime.strptime(value, "%Y-%m-%d")
        return value
    except ValueError:
        raise argparse.ArgumentTypeError(f"日期格式应为 YYYY-MM-DD: {value}")


def main():
    parser = argparse.ArgumentParser(description="按时间段生成 QA 知识（原 generate_qa.py）")
    parser.add_argument("--start", type=_valid_date, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", type=_valid_date, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--qa-excel", action="store_true", help="额外把 QA 结果导出为 Excel")
    parser.add_argument("--enrich-excel", metavar="XLSX",
                        help="手动模式：读取 QA Excel 的 matched_issue_ids 反查 GBase 明细")
    args = parser.parse_args()

    if args.enrich_excel:
        enrich_qa_excel(args.enrich_excel)
        return
    if not args.start or not args.end:
        parser.error("需要同时提供 --start 和 --end（或用 --enrich-excel 走辅助模式）")
    if args.start > args.end:
        parser.error("--start 不能晚于 --end")
    process_non_vendor_data(args.start, args.end, save_qa_excel=args.qa_excel)


if __name__ == "__main__":
    main()
