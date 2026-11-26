"""app 入库 MySQL：4 张业务表的「先删后写」。

对外只暴露 save_issue_results(final_df)：业务层把模型解析结果拼成 DataFrame 后
调用一次，本模块按 fldColumn4（厂商处理意见）是否有效分流写入 4 张表。

返回值仍是 ``{表名: 写入行数}``；``0`` 表示该分流没有数据、无需写入，
``-1`` 表示该表写入异常（已记 ERROR 日志），调用方据此决定是否告警。
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

# 表 1 / 表 2 的「目标列 → 来源列」；key_issues / key_word 都取 symptom
ISSUE_COLUMNS = {
    "task_oid": "task_oid",
    "business": "business",
    "last_time": "last_time",
    "subclass": "subclass",
    "create_time": "create_time",
    "response_time": "response_time",
    "error_msg": "error_msg",
    "key_issues": "symptom",
}
CLOUD_COLUMNS = {
    "task_oid": "task_oid",
    "key_word": "symptom",
    "last_time": "last_time",
    "create_time": "create_time",
    "response_time": "response_time",
    "error_msg": "error_msg",
}


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


def _project(df: pd.DataFrame, columns: dict, drop_na_col: str = None) -> pd.DataFrame:
    """按列映射挑列重命名；可选地丢弃某列为空/空串的行。"""
    out = pd.DataFrame({dest: df[src] for dest, src in columns.items()})
    if drop_na_col:
        out = out.dropna(subset=[drop_na_col])
        out = out[out[drop_na_col] != ""]
    return out


def _write_table(table_name: str, df: pd.DataFrame, task_oid_list: list) -> int:
    """统一的「写一张表」入口：成功返回行数，异常记 ERROR 并返回 -1。"""
    try:
        written = _append(table_name, df, task_oid_list)
    except Exception as e:
        logger.error(f"写入 {table_name} 失败: {e}")
        return -1
    if written:
        logger.info(f"写入表: {table_name} ({written}条)")
    return written


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
        counts[TABLE_ISSUES] = _write_table(
            TABLE_ISSUES, _project(df_valid, ISSUE_COLUMNS, drop_na_col="key_issues"), valid_oids
        )
        counts[TABLE_WORD_CLOUD] = _write_table(
            TABLE_WORD_CLOUD, _project(df_valid, CLOUD_COLUMNS, drop_na_col="key_word"), valid_oids
        )
        counts[TABLE_QA_RECORDS] = _write_table(
            TABLE_QA_RECORDS,
            df_valid.drop(columns=["fldColumn4"], errors="ignore"),
            valid_oids,
        )

    # 无厂商处理意见的数据单独入表，供 QA 生成读取
    if not df_invalid.empty:
        invalid_oids = df_invalid["task_oid"].unique().tolist()
        counts[TABLE_NON_VENDOR] = _write_table(
            TABLE_NON_VENDOR,
            df_invalid.drop(columns=["fldColumn4"], errors="ignore"),
            invalid_oids,
        )

    return counts
