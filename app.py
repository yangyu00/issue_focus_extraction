#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import time
import datetime
import requests
import pandas as pd
import json
import re
from typing import Dict, Any, List
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm.auto import tqdm
from sqlalchemy import create_engine, text
from utils import logger, setup_logging
from config import INFO_LOG_NAME, ERROR_LOG_NAME

# 配置日志
setup_logging(INFO_LOG_NAME, ERROR_LOG_NAME)

import schedule

# 引入原有的 dify 请求
from dify import dify_request

# ================= 配置区域 =================
DB_CONFIG = {
    "host": "134.84.22.233",
    "port": 8086,
    "user": "root",
    "password": "Nttslgkpt*2024",
    "database": "ntt_xiaoxin_agent",
    "charset": "utf8mb4"
}

API_URL = "http://134.84.22.233:6308/xxa/queryListByTime"
MAX_WORKERS = 1

# 优化: 增加 pool_recycle，防止 MySQL 连接在长时间空闲后断开
db_url = f"mysql+pymysql://{DB_CONFIG['user']}:{DB_CONFIG['password']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}?charset={DB_CONFIG['charset']}"
engine = create_engine(db_url, pool_recycle=3600, pool_pre_ping=True)


# ================= 工具函数 =================
def _to_text(x: Any) -> str:
    if x is None:
        return "其他"
    s = str(x).strip()
    if s=="" or s.lower() == "nan" or s == "无" or s == "无无":
        return "其他"
    return s


def clean_old_records(engine, table_name: str, task_oid_list: list):
    """
    在写入前，先删除数据库中已存在的 taskOid 记录，防止重复。
    """
    if not task_oid_list:
        return

    ids_formatted = ",".join([f"'{str(oid)}'" for oid in task_oid_list])
    # 注意：task_oid 若包含特殊字符需额外处理，此处假设为安全字符
    sql = f"DELETE FROM {table_name} WHERE task_oid IN ({ids_formatted})"

    try:
        with engine.connect() as conn:
            conn.execute(text(sql))
            conn.commit()
    except Exception as e:
        logger.error(f"清理旧数据失败 table={table_name}: {e}")


def dedup_device_value(value: str) -> str:
    """去除重复字符串"""
    if not isinstance(value, str):
        return value
    if len(value) < 2:
        return value
    if len(value) % 2 != 0:
        return value
    half = len(value) // 2
    left, right = value[:half], value[half:]
    return left if left == right else value


def _safe_json_loads(s: str) -> Dict[str, Any]:
    if not isinstance(s, str) or not s.strip():
        return {}
    s2 = s.strip()
    # s2 = re.sub(r"^```(?:json)?\s*|\s*```$", "", s2, flags=re.IGNORECASE).strip()
    try:
        return json.loads(s2)
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
    """构建输入给大模型的文本"""
    parts = []
    mapping = {
        "标题": row.get("taskTitle", ""),
        "描述": row.get("fldFaultcontent", ""),
        "分公司处理意见": row.get("fldColumn2", ""),
        "厂商处理意见": row.get("fldColumn4", "")
    }

    for label, val in mapping.items():
        val_str = _to_text(val)
        if not val_str:
            val_str = "无"
        parts.append(f"{label}：{val_str}")

    joined = "\n".join(parts).strip()
    return re.sub(r"\n{3,}", "\n\n", joined)


def process_model_output_for_db(model_out: str) -> pd.Series:
    """解析模型输出 JSON"""
    data = _safe_json_loads(model_out)

    device = dedup_device_value(_to_text(data.get("device", "")))
    business = _to_text(data.get("business", ""))
    subclass = _to_text(data.get("subclass", ""))
    symptom = _to_text(data.get("symptom", ""))
    symptom_detail = _to_text(data.get("symptom_detail", ""))
    cause = _to_text(data.get("cause", ""))

    branch_actions = _actions_to_str(data.get("branch_actions", ""))
    vendor_actions = _actions_to_str(data.get("vendor_actions", ""))

    return pd.Series({
        "device": device,
        "business": business,
        "subclass": subclass,
        "symptom": symptom,
        "symptom_detail": symptom_detail,
        "cause": cause,
        "branch_actions": branch_actions,
        "vendor_actions": vendor_actions
    })


# ================= 核心处理逻辑 (修改版) =================

def safe_run_model(text: str) -> dict:
    """
    封装模型调用
    返回字典: { 'raw_json': str, 'response_time': int, 'error_msg': str }
    """
    start_time = time.time()
    result = {
        "raw_json": "{}",
        "response_time": 0,  # 毫秒
        "error_msg": ""
    }

    try:
        # 调用 dify.py 中的接口
        resp = dify_request(text)

        # 简单判断：如果返回空字典字符串，且原本不是空的，可能 dify 内部重试失败了
        if resp == "{}" or not resp:
            pass
        print(f"输入：{text}, 输出：{resp}")
        result["raw_json"] = resp

    except Exception as e:
        error_str = str(e)
        logger.error(f"模型调用抛出异常: {error_str} | 输入片段: {text[:50]}...")
        # 截断错误信息到 1024 字符
        result["error_msg"] = error_str[:1024]
        result["raw_json"] = "{}"
    finally:
        end_time = time.time()
        # 计算毫秒数
        duration_ms = int((end_time - start_time) * 1000)
        result["response_time"] = duration_ms

    time.sleep(5)

    return result


def parallel_process(texts: List[str]) -> List[dict]:
    """并发调用模型，返回包含元数据的列表"""
    # 初始化结果列表
    results = [{"raw_json": "{}", "response_time": 0, "error_msg": "Init Failed"}] * len(texts)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
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
                    "error_msg": f"Thread Error: {str(e)}"[:1024]
                }
    return results


# ================= 业务逻辑 =================

def fetch_data_from_api(start_time: str, end_time: str) -> pd.DataFrame:
    """调用接口获取数据"""
    print(f"正在获取数据: {start_time} 至 {end_time}")
    params = {
        "startTime": start_time,
        "endTime": end_time
    }
    try:
        resp = requests.get(API_URL, params=params, timeout=60)
        try:
            data = resp.json()
        except json.JSONDecodeError:
            msg = f"接口返回非JSON格式: {resp.text[:100]}"
            logger.error(msg)
            return pd.DataFrame()

        if data.get("code") == 200 and data.get("success"):
            result_list = data.get("result", [])
            if not result_list:
                logger.info(f"接口返回数据为空 ({start_time})")
                return pd.DataFrame()
            print(f"成功获取 {len(result_list)} 条数据")
            return pd.DataFrame(result_list)
        else:
            msg = f"接口业务报错: {data.get('message')}"
            logger.error(msg)
            return pd.DataFrame()

    except Exception as e:
        logger.error(f"接口请求网络异常: {e}")
        print(f"接口请求异常: {e}")
        return pd.DataFrame()


def run_process_for_date(target_date_str: str):
    """
    执行指定日期的任务逻辑
    target_date_str: "YYYY-MM-DD"
    """
    try:
        # 1. 构造时间范围 (00:00:00 - 23:59:59)
        start_time_str = f"{target_date_str} 00:00:00"
        end_time_str = f"{target_date_str} 23:59:59"

        logger.info(f"开始执行任务，处理日期: {target_date_str}")

        # 2. 获取数据
        df = fetch_data_from_api(start_time_str, end_time_str)

        if df.empty:
            print(f"[{target_date_str}] 无数据需处理。")
            return

        # 3. 校验必要字段
        required_columns = ["taskOid", "fldColumn4"]
        missing_cols = [col for col in required_columns if col not in df.columns]
        if missing_cols:
            logger.error(f"错误：数据中缺失必要字段 {missing_cols}")
            return

        # ================= 过滤 fldColumn4 为空 (修改：不再全局过滤，而是标记) =================
        # initial_count = len(df)

        def is_valid_content(val):
            if val is None: return False
            s = str(val).strip()
            if s == "" or s.lower() == "nan": return False
            return True

        # 修改：不再此处过滤，而是全部保留，后面分表存储
        # df = df[df["fldColumn4"].apply(is_valid_content)].copy()
        # if df.empty:
        #     print(f"[{target_date_str}] 所有数据的 fldColumn4 均为空，跳过。")
        #     return
        # ==========================================================

        # 4. 构建输入
        print(f"[{target_date_str}] 构建模型输入并并发调用...")
        model_inputs = df.apply(build_labeled_input, axis=1).tolist()
        total_calls = len(model_inputs)

        # 5. 并发处理 (返回 List[dict])
        results_list = parallel_process(model_inputs)

        # 6. 解析结果合并
        print(f"[{target_date_str}] 解析模型结果...")

        # 将结果列表转为 DataFrame (包含 raw_json, response_time, error_msg)
        meta_df = pd.DataFrame(results_list)

        # 解析 JSON 内容
        extracted_cols = meta_df["raw_json"].apply(process_model_output_for_db)

        # 重置索引对齐
        df = df.reset_index(drop=True)
        extracted_cols = extracted_cols.reset_index(drop=True)
        mask_invalid = ~df["fldColumn4"].apply(is_valid_content)
        extracted_cols.loc[mask_invalid, "vendor_actions"]=""

        # 最终合并
        final_df = pd.concat(
            [
                df["taskOid"].rename("task_oid"),
                df["lastTime"].rename("last_time"),
                # 保留原始 fldColumn4 用于后续分流
                df["fldColumn4"],
                extracted_cols,
                meta_df[["response_time", "error_msg"]]
            ],
            axis=1
        )

        # 【修改】统一添加 create_time (字符串格式: yyyy-MM-dd HH:mm:ss)
        current_time_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        final_df["create_time"] = current_time_str

        # 7. 写入数据库
        current_task_oids = final_df["task_oid"].unique().tolist()
        if not current_task_oids:
            return
        # 分流数据
        mask_valid = final_df["fldColumn4"].apply(is_valid_content)
        df_valid = final_df[mask_valid].copy()
        df_invalid = final_df[~mask_valid].copy()

        # 处理有效数据 df_valid 到原有表
        if not df_valid.empty:
            valid_task_oids = df_valid["task_oid"].unique().tolist()
            
            # --- 表1: 焦点问题记录 (xxa_issues_records) ---
            try:
                df_issues = pd.DataFrame()
                df_issues["task_oid"] = df_valid["task_oid"]
                df_issues["business"] = df_valid["business"]
                df_issues["last_time"] = df_valid["last_time"]
                df_issues["subclass"] = df_valid["subclass"]
                # 新增字段
                df_issues["create_time"] = df_valid["create_time"]
                df_issues["response_time"] = df_valid["response_time"]
                df_issues["error_msg"] = df_valid["error_msg"]

                # df_issues["key_issues"] = df_valid.apply(lambda x: f"{x['device']}{x['symptom']}", axis=1)
                df_issues["key_issues"] = df_valid.apply(lambda x: x['symptom'], axis=1)

                df_issues = df_issues.dropna(subset=["key_issues"])
                df_issues = df_issues[df_issues["key_issues"] != ""]

                if not df_issues.empty:
                    clean_old_records(engine, "xxa_issues_records", valid_task_oids)
                    df_issues.to_sql("xxa_issues_records", engine, if_exists="append", index=False)
                    print(f"[{target_date_str}] 写入表: xxa_issues_records ({len(df_issues)}条)")
            except Exception as e:
                logger.error(f"写入 xxa_issues_records 失败: {e}")

            # --- 表2: 词云数据 (xxa_complaint_word_cloud) ---
            try:
                df_cloud = pd.DataFrame()
                df_cloud["task_oid"] = df_valid["task_oid"]
                df_cloud["key_word"] = df_valid["symptom"]
                df_cloud["last_time"] = df_valid["last_time"]

                # 新增字段
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

            # --- 表3: 全量问答记录 (xxa_bomc_qa_records) ---
            try:
                # 移除临时的辅助列再写入
                cols_to_drop = ["fldColumn4"]
                df_valid_write = df_valid.drop(columns=[c for c in cols_to_drop if c in df_valid.columns])
                
                clean_old_records(engine, "xxa_bomc_qa_records", valid_task_oids)
                # 全量写入
                df_valid_write.to_sql("xxa_bomc_qa_records", engine, if_exists="append", index=False)
                print(f"[{target_date_str}] 写入表: xxa_bomc_qa_records ({len(df_valid_write)}条)")
            except Exception as e:
                logger.error(f"写入 xxa_bomc_qa_records 失败: {e}")

        # 处理无效数据 df_invalid 到新表 xxa_bomc_qa_non_vendor
        if not df_invalid.empty:
            invalid_task_oids = df_invalid["task_oid"].unique().tolist()
            try:
                # 移除临时的辅助列再写入
                cols_to_drop = ["fldColumn4"]
                df_invalid_write = df_invalid.drop(columns=[c for c in cols_to_drop if c in df_invalid.columns])

                clean_old_records(engine, "xxa_bomc_qa_non_vendor", invalid_task_oids)
                df_invalid_write.to_sql("xxa_bomc_qa_non_vendor", engine, if_exists="append", index=False)
                print(f"[{target_date_str}] 写入表: xxa_bomc_qa_non_vendor ({len(df_invalid_write)}条)")
            except Exception as e:
                logger.error(f"写入 xxa_bomc_qa_non_vendor 失败: {e}")

        print(f"? [{target_date_str}] 完成！调用量: {total_calls}")

    except Exception as e:
        logger.error(f"[{target_date_str}] 执行异常: {e}", exc_info=True)


def run_daily_job():
    """默认运行昨天的任务"""
    now = datetime.datetime.now()
    yesterday = now - datetime.timedelta(days=1)
    date_str = yesterday.strftime("%Y-%m-%d")
    print(f"执行默认每日任务，日期: {date_str}")
    run_process_for_date(date_str)


def run_batch_job(start_date_str: str, end_date_str: str):
    """
    批量运行一段时间的数据
    start_date_str: '2024-11-01'
    end_date_str: '2024-11-30'
    """
    print(f"=== 开始批量任务: {start_date_str} 至 {end_date_str} ===")

    start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d")
    end_date = datetime.datetime.strptime(end_date_str, "%Y-%m-%d")

    curr = start_date
    while curr <= end_date:
        curr_str = curr.strftime("%Y-%m-%d")
        run_process_for_date(curr_str)
        curr += datetime.timedelta(days=1)
        # 稍微停顿一下防止请求过于密集
        time.sleep(2)

    print("=== 批量任务结束 ===")


# ================= 调度入口 =================

if __name__ == "__main__":
    # 场景1: 跑历史数据（例如跑 2024年11月整月）
    # 请取消下面这行注释来运行
    # run_batch_job("2025-11-01", "2025-11-30")

    # 场景2: 正常每日运行（跑昨天的）
    print("=== 服务已启动，等待定时任务触发 ===")

    # 立即运行一次（可选，为了防止启动后要等24小时才跑第一次）
    run_daily_job()

    # 设定每天凌晨 01:00 执行
    schedule.every().day.at("01:00").do(run_daily_job)

    # 无限循环，保持脚本不退出
    while True:
        try:
            schedule.run_pending()
            time.sleep(60)  # 每分钟检查一次
        except Exception as e:
            logger.error(f"调度器主循环异常：{e}")
            time.sleep(60)
