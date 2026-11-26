#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小信智能体摘要提取 —— 工单解析入库（单入口）。

合并了原 app.py（每日定时）与 app_batch.py（指定时段批量补跑）：
    python app.py                          # 默认：立即跑昨天 + 每天定时调度
    python app.py --mode daily             # 只跑昨天一次
    python app.py --mode date --date 2026-04-01
    python app.py --mode batch --start 2026-04-01 --end 2026-04-28

模型调用不再经过 Dify，改为 issue_workflow.run_extraction() 直连大模型。
"""
import argparse
import datetime
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

import pandas as pd
import requests
import schedule
from sqlalchemy import create_engine, text
from tqdm.auto import tqdm

from config import (
    BACKEND_API_URL,
    BATCH_DAY_SLEEP,
    ERROR_LOG_NAME,
    INFO_LOG_NAME,
    MAX_WORKERS,
    MYSQL_CONFIG,
    POST_CALL_SLEEP,
    SCHEDULE_TIME,
    mysql_url,
)
from issue_workflow import run_extraction
from utils import logger, setup_logging

setup_logging(INFO_LOG_NAME, ERROR_LOG_NAME)

db_url = mysql_url(MYSQL_CONFIG)
engine = create_engine(db_url, pool_recycle=3600, pool_pre_ping=True)


# ================= 工具函数 =================
def _to_text(x: Any) -> str:
    if x is None:
        return "其他"
    s = str(x).strip()
    if s == "" or s.lower() == "nan" or s == "无" or s == "无无":
        return "其他"
    return s


def clean_old_records(engine_, table_name: str, task_oid_list: list):
    """写入前删除已存在的 taskOid 记录，防止重复。"""
    if not task_oid_list:
        return
    ids_formatted = ",".join([f"'{str(oid)}'" for oid in task_oid_list])
    sql = f"DELETE FROM {table_name} WHERE task_oid IN ({ids_formatted})"
    try:
        with engine_.connect() as conn:
            conn.execute(text(sql))
            conn.commit()
    except Exception as e:
        logger.error(f"清理旧数据失败 table={table_name}: {e}")


def dedup_device_value(value: str) -> str:
    """去除重复字符串（模型偶发把同一设备名输出两遍）。"""
    if not isinstance(value, str):
        return value
    if len(value) < 2 or len(value) % 2 != 0:
        return value
    half = len(value) // 2
    left, right = value[:half], value[half:]
    return left if left == right else value


def _safe_json_loads(s: str) -> Dict[str, Any]:
    if not isinstance(s, str) or not s.strip():
        return {}
    try:
        return json.loads(s.strip())
    except Exception:
        return {}


def _actions_to_str(val: Any) -> str:
    try:
        if isinstance(val, (list, dict)):
            return json.dumps(val, ensure_ascii=False)
        return _to_text(val)
    except Exception:
        return _to_text(val)


def build_labeled_input(row: pd.Series) -> str:
    """构建输入给大模型的文本（与原工作流 sys.query 一致）。"""
    parts = []
    mapping = {
        "标题": row.get("taskTitle", ""),
        "描述": row.get("fldFaultcontent", ""),
        "分公司处理意见": row.get("fldColumn2", ""),
        "厂商处理意见": row.get("fldColumn4", ""),
    }
    for label, val in mapping.items():
        val_str = _to_text(val)
        if not val_str:
            val_str = "无"
        parts.append(f"{label}：{val_str}")
    joined = "\n".join(parts).strip()
    return re.sub(r"\n{3,}", "\n\n", joined)


def process_model_output_for_db(model_out: str) -> pd.Series:
    """解析模型输出 JSON 为入库字段。"""
    data = _safe_json_loads(model_out)
    return pd.Series({
        "device": dedup_device_value(_to_text(data.get("device", ""))),
        "business": _to_text(data.get("business", "")),
        "subclass": _to_text(data.get("subclass", "")),
        "symptom": _to_text(data.get("symptom", "")),
        "symptom_detail": _to_text(data.get("symptom_detail", "")),
        "cause": _to_text(data.get("cause", "")),
        "branch_actions": _actions_to_str(data.get("branch_actions", "")),
        "vendor_actions": _actions_to_str(data.get("vendor_actions", "")),
    })


# ================= 模型调用（改为直连） =================
def safe_run_model(text_: str) -> dict:
    """执行一条工单的完整提取工作流。

    返回 { 'raw_json': str, 'response_time': int(毫秒), 'error_msg': str }
    """
    start_time = time.time()
    result = {"raw_json": "{}", "response_time": 0, "error_msg": ""}
    try:
        raw_json, error_msg = run_extraction(text_)
        result["raw_json"] = raw_json or "{}"
        result["error_msg"] = error_msg
        print(f"输入：{text_}, 输出：{raw_json}")
    except Exception as e:
        error_str = str(e)
        logger.error(f"模型调用抛出异常: {error_str} | 输入片段: {text_[:50]}...")
        result["error_msg"] = error_str[:1024]
        result["raw_json"] = "{}"
    finally:
        result["response_time"] = int((time.time() - start_time) * 1000)

    if POST_CALL_SLEEP > 0:
        time.sleep(POST_CALL_SLEEP)
    return result


def parallel_process(texts: List[str]) -> List[dict]:
    """工单级并发调用，返回包含元数据的列表。"""
    results = [{"raw_json": "{}", "response_time": 0, "error_msg": "Init Failed"} for _ in texts]
    with ThreadPoolExecutor(max_workers=max(1, MAX_WORKERS)) as ex:
        futures = {ex.submit(safe_run_model, txt): i for i, txt in enumerate(texts)}
        for fut in tqdm(as_completed(futures), total=len(texts), desc="AI处理中"):
            i = futures[fut]
            try:
                results[i] = fut.result()
            except Exception as e:
                logger.error(f"线程执行异常 Index {i}: {e}")
                results[i] = {
                    "raw_json": "{}",
                    "response_time": 0,
                    "error_msg": f"Thread Error: {str(e)}"[:1024],
                }
    return results


# ================= 业务逻辑 =================
def fetch_data_from_api(start_time: str, end_time: str) -> pd.DataFrame:
    """调用后端接口取数。"""
    print(f"正在获取数据: {start_time} 至 {end_time}")
    params = {"startTime": start_time, "endTime": end_time}
    try:
        resp = requests.get(BACKEND_API_URL, params=params, timeout=60)
        try:
            data = resp.json()
        except json.JSONDecodeError:
            logger.error(f"接口返回非JSON格式: {resp.text[:100]}")
            return pd.DataFrame()

        if data.get("code") == 200 and data.get("success"):
            result_list = data.get("result", [])
            if not result_list:
                logger.info(f"接口返回数据为空 ({start_time})")
                return pd.DataFrame()
            print(f"成功获取 {len(result_list)} 条数据")
            return pd.DataFrame(result_list)
        logger.error(f"接口业务报错: {data.get('message')}")
        return pd.DataFrame()
    except Exception as e:
        logger.error(f"接口请求网络异常: {e}")
        print(f"接口请求异常: {e}")
        return pd.DataFrame()


def is_valid_content(val) -> bool:
    if val is None:
        return False
    s = str(val).strip()
    return not (s == "" or s.lower() == "nan")


def run_process_for_date(target_date_str: str):
    """执行指定日期（YYYY-MM-DD）的取数 → 提取 → 分流入库。"""
    try:
        start_time_str = f"{target_date_str} 00:00:00"
        end_time_str = f"{target_date_str} 23:59:59"
        logger.info(f"开始执行任务，处理日期: {target_date_str}")

        df = fetch_data_from_api(start_time_str, end_time_str)
        if df.empty:
            print(f"[{target_date_str}] 无数据需处理。")
            return

        required_columns = ["taskOid", "fldColumn4"]
        missing_cols = [col for col in required_columns if col not in df.columns]
        if missing_cols:
            logger.error(f"错误：数据中缺失必要字段 {missing_cols}")
            return

        print(f"[{target_date_str}] 构建模型输入并并发调用...")
        model_inputs = df.apply(build_labeled_input, axis=1).tolist()
        total_calls = len(model_inputs)

        results_list = parallel_process(model_inputs)
        print(f"[{target_date_str}] 解析模型结果...")

        meta_df = pd.DataFrame(results_list)
        extracted_cols = meta_df["raw_json"].apply(process_model_output_for_db)

        df = df.reset_index(drop=True)
        extracted_cols = extracted_cols.reset_index(drop=True)
        mask_invalid = ~df["fldColumn4"].apply(is_valid_content)
        extracted_cols.loc[mask_invalid, "vendor_actions"] = ""

        final_df = pd.concat(
            [
                df["taskOid"].rename("task_oid"),
                df["lastTime"].rename("last_time"),
                df["fldColumn4"],
                extracted_cols,
                meta_df[["response_time", "error_msg"]],
            ],
            axis=1,
        )
        final_df["create_time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        current_task_oids = final_df["task_oid"].unique().tolist()
        if not current_task_oids:
            return

        mask_valid = final_df["fldColumn4"].apply(is_valid_content)
        df_valid = final_df[mask_valid].copy()
        df_invalid = final_df[~mask_valid].copy()

        if not df_valid.empty:
            valid_task_oids = df_valid["task_oid"].unique().tolist()

            # 表1：焦点问题记录
            try:
                df_issues = pd.DataFrame()
                df_issues["task_oid"] = df_valid["task_oid"]
                df_issues["business"] = df_valid["business"]
                df_issues["last_time"] = df_valid["last_time"]
                df_issues["subclass"] = df_valid["subclass"]
                df_issues["create_time"] = df_valid["create_time"]
                df_issues["response_time"] = df_valid["response_time"]
                df_issues["error_msg"] = df_valid["error_msg"]
                df_issues["key_issues"] = df_valid["symptom"]
                df_issues = df_issues.dropna(subset=["key_issues"])
                df_issues = df_issues[df_issues["key_issues"] != ""]
                if not df_issues.empty:
                    clean_old_records(engine, "xxa_issues_records", valid_task_oids)
                    df_issues.to_sql("xxa_issues_records", engine, if_exists="append", index=False)
                    print(f"[{target_date_str}] 写入表: xxa_issues_records ({len(df_issues)}条)")
            except Exception as e:
                logger.error(f"写入 xxa_issues_records 失败: {e}")

            # 表2：词云数据
            try:
                df_cloud = pd.DataFrame()
                df_cloud["task_oid"] = df_valid["task_oid"]
                df_cloud["key_word"] = df_valid["symptom"]
                df_cloud["last_time"] = df_valid["last_time"]
                df_cloud["create_time"] = df_valid["create_time"]
                df_cloud["response_time"] = df_valid["response_time"]
                df_cloud["error_msg"] = df_valid["error_msg"]
                df_cloud = df_cloud.dropna(subset=["key_word"])
                df_cloud = df_cloud[df_cloud["key_word"] != ""]
                if not df_cloud.empty:
                    clean_old_records(engine, "xxa_complaint_word_cloud", valid_task_oids)
                    df_cloud.to_sql("xxa_complaint_word_cloud", engine, if_exists="append", index=False)
                    print(f"[{target_date_str}] 写入表: xxa_complaint_word_cloud ({len(df_cloud)}条)")
            except Exception as e:
                logger.error(f"写入 xxa_complaint_word_cloud 失败: {e}")

            # 表3：全量问答记录
            try:
                df_valid_write = df_valid.drop(columns=["fldColumn4"], errors="ignore")
                clean_old_records(engine, "xxa_bomc_qa_records", valid_task_oids)
                df_valid_write.to_sql("xxa_bomc_qa_records", engine, if_exists="append", index=False)
                print(f"[{target_date_str}] 写入表: xxa_bomc_qa_records ({len(df_valid_write)}条)")
            except Exception as e:
                logger.error(f"写入 xxa_bomc_qa_records 失败: {e}")

        # 无厂商处理意见的数据单独入表
        if not df_invalid.empty:
            invalid_task_oids = df_invalid["task_oid"].unique().tolist()
            try:
                df_invalid_write = df_invalid.drop(columns=["fldColumn4"], errors="ignore")
                clean_old_records(engine, "xxa_bomc_qa_non_vendor", invalid_task_oids)
                df_invalid_write.to_sql("xxa_bomc_qa_non_vendor", engine, if_exists="append", index=False)
                print(f"[{target_date_str}] 写入表: xxa_bomc_qa_non_vendor ({len(df_invalid_write)}条)")
            except Exception as e:
                logger.error(f"写入 xxa_bomc_qa_non_vendor 失败: {e}")

        print(f"✅ [{target_date_str}] 完成！调用量: {total_calls}")
    except Exception as e:
        logger.error(f"[{target_date_str}] 执行异常: {e}", exc_info=True)


def run_daily_job():
    """默认跑昨天。"""
    yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
    date_str = yesterday.strftime("%Y-%m-%d")
    print(f"执行默认每日任务，日期: {date_str}")
    run_process_for_date(date_str)


def run_batch_job(start_date_str: str, end_date_str: str):
    """按天批量补跑一段时间。"""
    print(f"=== 开始批量任务: {start_date_str} 至 {end_date_str} ===")
    start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d")
    end_date = datetime.datetime.strptime(end_date_str, "%Y-%m-%d")
    curr = start_date
    while curr <= end_date:
        run_process_for_date(curr.strftime("%Y-%m-%d"))
        curr += datetime.timedelta(days=1)
        if BATCH_DAY_SLEEP > 0:
            time.sleep(BATCH_DAY_SLEEP)
    print("=== 批量任务结束 ===")


# ================= 调度 / 命令行入口 =================
def _valid_date(value):
    try:
        datetime.datetime.strptime(value, "%Y-%m-%d")
        return value
    except ValueError:
        raise argparse.ArgumentTypeError(f"日期格式应为 YYYY-MM-DD: {value}")


def main():
    parser = argparse.ArgumentParser(
        description="工单解析入库（原 app.py + app_batch.py 合并后的单入口）"
    )
    parser.add_argument(
        "--mode", choices=["schedule", "daily", "date", "batch"], default="schedule",
        help="schedule=立即跑昨天+每日定时(默认)；daily=只跑昨天；date=跑指定单日；batch=跑日期区间",
    )
    parser.add_argument("--date", type=_valid_date, help="--mode date 的日期 YYYY-MM-DD")
    parser.add_argument("--start", type=_valid_date, help="--mode batch 的起始日期 YYYY-MM-DD")
    parser.add_argument("--end", type=_valid_date, help="--mode batch 的结束日期 YYYY-MM-DD")
    parser.add_argument("--at", default=SCHEDULE_TIME, help="schedule 模式的每日执行时间，默认 01:00")
    parser.add_argument("--no-run-now", action="store_true", help="schedule 模式启动时跳过「立即跑昨天」")
    args = parser.parse_args()

    if args.mode == "batch":
        if not args.start or not args.end:
            parser.error("--mode batch 需要同时提供 --start 和 --end")
        if args.start > args.end:
            parser.error("--start 不能晚于 --end")
        run_batch_job(args.start, args.end)
        return

    if args.mode == "date":
        if not args.date:
            parser.error("--mode date 需要提供 --date")
        run_process_for_date(args.date)
        return

    if args.mode == "daily":
        run_daily_job()
        return

    # schedule：保持原 app.py 行为
    print("=== 服务已启动，等待定时任务触发 ===")
    if not args.no_run_now:
        run_daily_job()
    schedule.every().day.at(args.at).do(run_daily_job)
    while True:
        try:
            schedule.run_pending()
            time.sleep(60)
        except Exception as e:
            logger.error(f"调度器主循环异常：{e}")
            time.sleep(60)


if __name__ == "__main__":
    main()
