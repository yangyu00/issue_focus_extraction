"""QA 知识 MySQL：读取非厂商工单、按 id 反查、写入标准问答对。"""
import pandas as pd
from sqlalchemy import text

from xiaoxin_work_order.common import format_issue_ids
from xiaoxin_work_order.db.connections import qa_connection, qa_engine
from xiaoxin_work_order.logging_setup import logger

NON_VENDOR_TABLE = "xxa_bomc_qa_non_vendor"  # QA 生成的输入表（app 库写入，此处只读）
QA_TABLE = "xxa_city_qa_knowledge"           # 标准问答对输出表


def read_non_vendor(start_time: str, end_time: str) -> pd.DataFrame:
    """按 last_time 读取指定日期范围内的非厂商工单（参数化查询）。"""
    sql = text(
        f"SELECT * FROM {NON_VENDOR_TABLE} "
        f"WHERE last_time >= :start_dt AND last_time <= :end_dt"
    )
    params = {
        "start_dt": f"{start_time} 00:00:00",
        "end_dt": f"{end_time} 23:59:59",
    }
    return pd.read_sql(sql, qa_engine(), params=params)


def get_records_by_ids(id_list) -> pd.DataFrame:
    """根据 id 列表查询非厂商工单（命名参数，兼容任意长度）。"""
    if not id_list:
        return pd.DataFrame()
    params = {f"id_{i}": value for i, value in enumerate(id_list)}
    placeholders = ",".join(f":{key}" for key in params)
    sql = text(f"SELECT * FROM {NON_VENDOR_TABLE} WHERE id IN ({placeholders})")
    try:
        return pd.read_sql(sql, qa_engine(), params=params)
    except Exception as e:
        logger.error(f"按 id 查询 {NON_VENDOR_TABLE} 失败: {e}")
        return pd.DataFrame()


def save_qa_knowledge(qa_data: list) -> int:
    """把聚类生成的标准问答对批量写入 xxa_city_qa_knowledge，返回写入条数。

    qa_data 每项：{standard_question, standard_answer, matched_issue_ids}
    固定列：knowledge_source / work_id / name / city / city_text 由本模块填充；
    matched_issue_ids 统一经 common.format_issue_ids() 变成逗号分隔字符串。
    """
    if not qa_data:
        return 0
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
    connection = qa_connection()
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
                format_issue_ids(item.get("matched_issue_ids", "")),
            ) for item in qa_data]
            cursor.executemany(sql, data_to_insert)
        connection.commit()
        logger.info(f"成功插入 {len(data_to_insert)} 条数据到 {QA_TABLE} 表。")
        return len(data_to_insert)
    except Exception as e:
        logger.error(f"数据库操作出错: {e}")
        connection.rollback()
        return 0
    finally:
        connection.close()
