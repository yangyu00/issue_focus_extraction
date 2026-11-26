"""app 入库 MySQL：4 张业务表的「先删后写」。

对外只暴露 save_issue_results(final_df)：业务层把模型解析结果拼成 DataFrame 后
调用一次，本模块按 fldColumn4（厂商处理意见）是否有效分流写入 4 张表。
"""
import pandas as pd
from sqlalchemy import bindparam, text

from xiaoxin_work_order.common import is_valid_content
from xiaoxin_work_order.db.connections import app_engine
from xiaoxin_work_order.logging_setup import logger

TABLE_ISSUES = "xxa_issues_records"            # 焦点问题记录
TABLE_WORD_CLOUD = "xxa_complaint_word_cloud"  # 投诉词云
TABLE_QA_RECORDS = "xxa_bomc_qa_records"       # 有厂商处理意见的全量问答记录
TABLE_NON_VENDOR = "xxa_bomc_qa_non_vendor"    # 无厂商处理意见，供 QA 聚类使用

APP_TABLES = (
    TABLE_ISSUES,
    TABLE_WORD_CLOUD,
    TABLE_QA_RECORDS,
    TABLE_NON_VENDOR,
)
_ALLOWED_TABLES = frozenset(APP_TABLES)


def clean_old_records(table_name: str, task_oid_list: list, engine=None):
    """写入前删除已存在的 taskOid 记录，防止重复（表名白名单 + 值参数化）。"""
    if not task_oid_list:
        return
    if table_name not in _ALLOWED_TABLES:
        raise ValueError(f"非法表名，拒绝执行 DELETE: {table_name}")
    stmt = text(
        f"DELETE FROM {table_name} WHERE task_oid IN :oids"
    ).bindparams(bindparam("oids", expanding=True))
    try:
        with (engine or app_engine()).connect() as conn:
            conn.execute(stmt, {"oids": [str(oid) for oid in task_oid_list]})
            conn.commit()
    except Exception as e:
        logger.error(f"清理旧数据失败 table={table_name}: {e}")


def _append(table_name: str, df: pd.DataFrame, task_oid_list: list) -> int:
    """删旧 + 追加写，返回写入行数。"""
    if df is None or df.empty:
        return 0
    clean_old_records(table_name, task_oid_list)
    df.to_sql(table_name, app_engine(), if_exists="append", index=False)
    return len(df)


def save_issue_results(final_df: pd.DataFrame) -> dict:
    """把一次日期批次的解析结果分流写入 4 张表，返回 {表名: 写入行数}。

    final_df 必需列：
        task_oid, last_time, fldColumn4, business, device, subclass,
        symptom, symptom_detail, cause, branch_actions, vendor_actions,
        create_time, response_time, error_msg
    分流规则：fldColumn4 有实质内容 → 前 3 张表；否则 → xxa_bomc_qa_non_vendor。
    """
    counts = {table: 0 for table in APP_TABLES}
    if final_df is None or final_df.empty:
        return counts
    if "fldColumn4" not in final_df.columns:
        raise ValueError("final_df 缺少分流列 fldColumn4")

    mask_valid = final_df["fldColumn4"].apply(is_valid_content)
    df_valid = final_df[mask_valid].copy()
    df_invalid = final_df[~mask_valid].copy()

    if not df_valid.empty:
        valid_oids = df_valid["task_oid"].unique().tolist()

        # 表1：焦点问题记录（key_issues 取 symptom）
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
            counts[TABLE_ISSUES] = _append(TABLE_ISSUES, df_issues, valid_oids)
            if counts[TABLE_ISSUES]:
                print(f"写入表: {TABLE_ISSUES} ({counts[TABLE_ISSUES]}条)")
        except Exception as e:
            logger.error(f"写入 {TABLE_ISSUES} 失败: {e}")

        # 表2：词云数据（key_word 取 symptom）
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
            counts[TABLE_WORD_CLOUD] = _append(TABLE_WORD_CLOUD, df_cloud, valid_oids)
            if counts[TABLE_WORD_CLOUD]:
                print(f"写入表: {TABLE_WORD_CLOUD} ({counts[TABLE_WORD_CLOUD]}条)")
        except Exception as e:
            logger.error(f"写入 {TABLE_WORD_CLOUD} 失败: {e}")

        # 表3：全量问答记录（去掉分流用的原始列 fldColumn4）
        try:
            df_valid_write = df_valid.drop(columns=["fldColumn4"], errors="ignore")
            counts[TABLE_QA_RECORDS] = _append(TABLE_QA_RECORDS, df_valid_write, valid_oids)
            if counts[TABLE_QA_RECORDS]:
                print(f"写入表: {TABLE_QA_RECORDS} ({counts[TABLE_QA_RECORDS]}条)")
        except Exception as e:
            logger.error(f"写入 {TABLE_QA_RECORDS} 失败: {e}")

    # 无厂商处理意见的数据单独入表，供 QA 生成读取
    if not df_invalid.empty:
        invalid_oids = df_invalid["task_oid"].unique().tolist()
        try:
            df_invalid_write = df_invalid.drop(columns=["fldColumn4"], errors="ignore")
            counts[TABLE_NON_VENDOR] = _append(TABLE_NON_VENDOR, df_invalid_write, invalid_oids)
            if counts[TABLE_NON_VENDOR]:
                print(f"写入表: {TABLE_NON_VENDOR} ({counts[TABLE_NON_VENDOR]}条)")
        except Exception as e:
            logger.error(f"写入 {TABLE_NON_VENDOR} 失败: {e}")

    return counts
