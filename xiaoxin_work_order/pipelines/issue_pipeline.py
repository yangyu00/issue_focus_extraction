"""流水线一：取数 → 8 字段提取 → 分流入库。

本文件只做编排与数据整形；所有数据库写入交给 xiaoxin_work_order.db.save_issue_results()。
字段清单唯一来源见 xiaoxin_work_order/fields.py；日志由 CLI 入口统一配置。
"""
import datetime
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

import pandas as pd
from tqdm.auto import tqdm

from xiaoxin_work_order.api.backend import fetch_data_from_api
from xiaoxin_work_order.common import dedup_device_value, is_valid_content, to_text
from xiaoxin_work_order.config import BATCH_DAY_SLEEP, MAX_WORKERS, POST_CALL_SLEEP
from xiaoxin_work_order.db import save_issue_results
from xiaoxin_work_order.extraction.workflow import run_extraction
from xiaoxin_work_order.fields import FIELDS
from xiaoxin_work_order.logging_setup import logger


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


def parse_model_output(model_out: str) -> pd.Series:
    """解析模型输出 JSON 为 8 个入库字段（纯解析，不碰数据库）。

    字段与顺序来自 fields.FIELDS；只有 device 与两类 actions 需要额外整形。
    """
    data = _safe_json_loads(model_out)
    cleaned = {key: to_text(data.get(key, "")) for key in FIELDS}
    cleaned["device"] = dedup_device_value(cleaned["device"])
    for key in ("branch_actions", "vendor_actions"):
        cleaned[key] = _actions_to_str(data.get(key, ""))
    return pd.Series(cleaned)


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
        logger.info(f"模型输出: {raw_json}")
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


# ================= 纯整形（可单独单测） =================
def _build_final_df(df: pd.DataFrame, results_list: List[dict]) -> pd.DataFrame:
    """把「接口原始数据 + 模型结果 + 元数据」拼成 save_issue_results 需要的 DataFrame。"""
    meta_df = pd.DataFrame(results_list)
    extracted_cols = meta_df["raw_json"].apply(parse_model_output)

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
    return final_df


# ================= 业务编排 =================
def fetch_for_date(target_date_str: str) -> pd.DataFrame:
    """仅取数（轻量探测用），不跑模型、不写库。

    时间窗固定为该日期的 00:00:00–23:59:59，与入库流程完全一致，
    保证「探测到的快照」和「最终入库的快照」口径相同。
    """
    start_time_str = f"{target_date_str} 00:00:00"
    end_time_str = f"{target_date_str} 23:59:59"
    return fetch_data_from_api(start_time_str, end_time_str)


def run_process_for_date(target_date_str: str, df=None):
    """执行指定日期（YYYY-MM-DD）的取数 → 提取 → 分流入库。

    df 不为 None 时直接使用已确认稳定的数据帧（由就绪探测传入），
    避免二次取数又拿到一份不同快照；为 None 时保持原有自行取数行为。
    """
    try:
        logger.info(f"开始执行任务，处理日期: {target_date_str}")

        if df is None:
            df = fetch_for_date(target_date_str)
        if df.empty:
            logger.info(f"[{target_date_str}] 无数据需处理。")
            return

        required_columns = ["taskOid", "fldColumn4"]
        missing_cols = [col for col in required_columns if col not in df.columns]
        if missing_cols:
            logger.error(f"错误：数据中缺失必要字段 {missing_cols}")
            return

        logger.info(f"[{target_date_str}] 构建模型输入并并发调用...")
        model_inputs = df.apply(build_labeled_input, axis=1).tolist()
        total_calls = len(model_inputs)

        results_list = parallel_process(model_inputs)
        logger.info(f"[{target_date_str}] 解析模型结果...")
        final_df = _build_final_df(df, results_list)

        current_task_oids = final_df["task_oid"].unique().tolist()
        if not current_task_oids:
            return

        # 数据库读写统一收口到 xiaoxin_work_order.db；接收返回值，别把「完成」当成必然
        counts = save_issue_results(final_df)
        logger.info(f"[{target_date_str}] 入库结果: {counts}")
        failed_tables = [table for table, written in counts.items() if written < 0]
        if failed_tables:
            logger.warning(f"[{target_date_str}] 部分表写入失败: {failed_tables}，请查 ERROR 日志")
        else:
            logger.info(f"✅ [{target_date_str}] 完成！调用量: {total_calls}")
    except Exception as e:
        logger.error(f"[{target_date_str}] 执行异常: {e}", exc_info=True)


def run_daily_job():
    """默认跑昨天。"""
    yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
    date_str = yesterday.strftime("%Y-%m-%d")
    logger.info(f"执行默认每日任务，日期: {date_str}")
    run_process_for_date(date_str)


def run_batch_job(start_date_str: str, end_date_str: str):
    """按天批量补跑一段时间（横幅为交互式输出，业务事件仍走 logger）。"""
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
