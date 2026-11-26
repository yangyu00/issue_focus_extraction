#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pymysql
import sys
import os

# 将项目根目录加入路径，以便导入 sql_query 模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sql_query import get_records_by_ids


# ================= GBase (opera 库) 配置 =================
GBASE_CONFIG = {
    "host": "134.85.16.139",
    "port": 5258,
    "user": "busiopera",
    "password": "Busiopera#2025",
    "db": "opera",
    "charset": "utf8"
}


def query_gbase_by_task_oids(task_oid_list: list):
    """
    根据 task_oid 列表，查询 GBase opera 库 xiaoxin 表中的
    taskTitle、fldFaultcontent、fldColumn2 字段并打印。
    
    :param task_oid_list: list，包含要查询的 TASK_OID 值
    """
    if not task_oid_list:
        print("task_oid 列表为空，跳过 GBase 查询。")
        return

    try:
        conn = pymysql.connect(**GBASE_CONFIG)
        cursor = conn.cursor()

        # 根据列表长度生成对应数量的 %s 占位符，防止 SQL 注入
        placeholders = ",".join(["%s"] * len(task_oid_list))
        query = f"""
            SELECT TASK_OID, TASK_TITLE, fld_faultcontent, fld_column_2
            FROM xiaoxin
            WHERE TASK_OID IN ({placeholders})
        """

        cursor.execute(query, tuple(task_oid_list))

        columns = [desc[0] for desc in cursor.description]
        results = cursor.fetchall()

        cursor.close()
        conn.close()

        if not results:
            print("GBase 中未查询到匹配的记录。")
            return

        print(f"\n成功从 GBase 查询到 {len(results)} 条记录：")
        for row in results:
            record = dict(zip(columns, row))
            print("-" * 40)
            for col_name, col_value in record.items():
                print(f"{col_name}: {col_value}")
        print("-" * 40)

    except pymysql.MySQLError as e:
        print(f"GBase 数据库连接或查询失败: {e}")


def main(target_ids):
    # Step 1：从 xxa_bomc_qa_non_vendor 表查询指定 id 的记录，获取 task_oid
    print(f"正在从 xxa_bomc_qa_non_vendor 查询 id 列表 {target_ids} 对应的记录...")

    non_vendor_df = get_records_by_ids(target_ids)

    if non_vendor_df.empty:
        print("xxa_bomc_qa_non_vendor 中未查询到对应记录，程序终止。")
        return

    if "task_oid" not in non_vendor_df.columns:
        print(f"查询结果中缺少 task_oid 列，现有列：{non_vendor_df.columns.tolist()}")
        return

    # Step 2：提取 task_oid 列表（去重、去空值）
    task_oid_list = non_vendor_df["task_oid"].dropna().unique().tolist()
    print(f"获取到 task_oid 列表：{task_oid_list}")

    # Step 3：查询 GBase opera 库，获取原始工单字段并打印
    query_gbase_by_task_oids(task_oid_list)


if __name__ == "__main__":
    target_ids = [88067, 88414, 88493, 88834, 89356, 89899, 90891, 91089, 91246, 91332, 91686, 91804, 92491, 92968, 92969, 93441, 93451, 93459, 93846, 94018, 88945, 90625, 91747, 92651, 92762, 92850]
    main(target_ids)
