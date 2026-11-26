"""GBase opera.xiaoxin 工单原始明细（只读）。

用于 QA 生成时按 task_oid 补齐「报障标题 / 报障内容 / 分公司处理意见」，
以及 --enrich-excel 手动模式反查明细。
"""
from xiaoxin_work_order.db.connections import gbase_connection
from xiaoxin_work_order.logging_setup import logger


def query_gbase_by_task_oids(task_oid_list: list) -> list:
    """按 TASK_OID 列表查询 GBase opera.xiaoxin 的原始字段，返回 dict 列表。

    返回字段：TASK_OID / TASK_TITLE / fld_faultcontent / fld_column_2
    """
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
        conn = gbase_connection()
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
