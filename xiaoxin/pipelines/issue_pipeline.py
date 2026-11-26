"""流水线一：取数 → 8 字段提取 → 分流入库。

本文件只做编排与数据整形；所有数据库写入交给 xiaoxin.db.save_issue_results()。
"""
import datetime
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

import pandas as pd
from tqdm.auto import tqdm

from xiaoxin.api.backend import fetch_data_from_api
from xiaoxin.common import dedup_device_value, is_valid_content, to_text
from xiaoxin.config import (
    BATCH_DAY_SLEEP,
    ERROR_LOG_NAME,
    INFO_LOG_NAME,
    MAX_WORKERS,
    POST_CALL_SLEEP,
)
from xiaoxin.db import save_issue_results
from xiaoxin.extraction.workflow import run_extraction
from xiaoxin.logging_setup import logger, setup_logging

setup_logging(INFO_LOG_NAME, ERROR_LOG_NAME)


# ================= 解析 / 整形 =================
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
        return to_text(val)
    except Exception:
        return to_text(val)


def build_labeled_input(row: pd.Series) -> str:
    """构建输入给大模型的文本。"""
    parts = []
    mapping = {
        "标题": row.get("taskTitle", ""),
        "描述": row.get("fldFaultcontent", ""),
        "分公司处理意见": row.get("fldColumn2", ""),
        "厂商处理意见": row.get("fldColumn4", ""),
    }
    for label, val in mapping.items():
        val_str = to_text(val)
        if not val_str:
            val_str = "无"
        parts.append(f"{label}：{val_str}")
    joined = "\n".join(parts).strip()
    return re.sub(r"\n{3,}", "\n\n", joined)


def process_model_output_for_db(model_out: str) -> pd.Series:
    """解析模型输出 JSON 为入库字段。"""
    data = _safe_json_loads(model_out)
    return pd.Series({
        "device": dedup_device_value(to_text(data.get("device", ""))),
        "business": to_text(data.get("business", "")),
        "subclass": to_text(data.get("subclass", "")),
        "symptom": to_text(data.get("symptom", "")),
        "symptom_detail": to_text(data.get("symptom_detail", "")),
        "cause": to_text(data.get("cause", "")),
        "branch_actions": _actions_to_str(data.get("branch_actions", "")),
        "vendor_actions": _actions_to_str(data.get("vendor_actions", "")),
    })


# ================= 模型调用 =================
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


# ================= 业务编排 =================
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

        # 数据库读写统一收口到 xiaoxin.db
        save_issue_results(final_df)

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
