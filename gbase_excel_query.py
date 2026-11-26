#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import pymysql
import pandas as pd
import ast
import os
import sys

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


def query_gbase_by_task_oids(task_oid_list: list) -> list:
    """
    根据 task_oid 列表，查询 GBase opera 库 xiaoxin 表中的
    TASK_TITLE、fld_faultcontent（报障内容）、fld_column_2 字段。

    :param task_oid_list: list，包含要查询的 TASK_OID 值
    :return: list of dict，每条记录为一个字典
    """
    if not task_oid_list:
        print("task_oid 列表为空，跳过 GBase 查询。")
        return []

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
            print(f"  GBase 中未查询到 {len(task_oid_list)} 个 task_oid 对应的记录。")
            return []

        return [dict(zip(columns, row)) for row in results]

    except pymysql.MySQLError as e:
        print(f"GBase 数据库连接或查询失败: {e}")
        return []


def parse_matched_issue_ids(value) -> list:
    """
    解析 matched_issue_ids 列的值，支持以下格式：
    - Python list 字符串: "['oid1', 'oid2']"
    - 逗号分隔字符串: "oid1,oid2"
    - 已经是 list 类型
    """
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
    # 按逗号分隔兜底
    return [v.strip() for v in value.split(",") if v.strip()]


def main(input_xlsx: str):
    """
    主函数：读取 xlsx 文件中 matched_issue_ids 列（存放的是 id 列表），
    先通过 get_records_by_ids 查到对应的 task_oid，再查询 GBase 获取原始工单字段，
    将每条 task_oid 的详细信息保存到新的 xlsx 文件。

    :param input_xlsx: 输入 xlsx 文件路径
    """
    # ---- 读取输入文件 ----
    if not os.path.exists(input_xlsx):
        print(f"文件不存在: {input_xlsx}")
        return

    print(f"读取文件: {input_xlsx}")
    df_input = pd.read_excel(input_xlsx)

    if "matched_issue_ids" not in df_input.columns:
        print(f"文件中缺少 'matched_issue_ids' 列，现有列：{df_input.columns.tolist()}")
        return

    # ---- Step 1：汇总所有 id（去重），matched_issue_ids 中存的是 id ----
    all_ids = set()
    for val in df_input["matched_issue_ids"]:
        ids = parse_matched_issue_ids(val)
        all_ids.update(ids)

    all_ids_list = [int(i) for i in all_ids if str(i).strip().isdigit()]
    print(f"共解析出 {len(all_ids_list)} 个唯一 id，正在查询 xxa_bomc_qa_non_vendor 获取 task_oid...")

    # ---- Step 2：通过 get_records_by_ids 查出对应的 task_oid ----
    non_vendor_df = get_records_by_ids(all_ids_list)

    if non_vendor_df.empty or "task_oid" not in non_vendor_df.columns:
        print("xxa_bomc_qa_non_vendor 中未查到对应记录或缺少 task_oid 列，程序终止。")
        return

    # 建立 id -> task_oid 的映射（id 转字符串统一比较）
    id_to_task_oid = {
        str(row["id"]): str(row["task_oid"])
        for _, row in non_vendor_df.iterrows()
        if pd.notna(row.get("task_oid")) and pd.notna(row.get("id"))
    }
    print(f"id -> task_oid 映射共 {len(id_to_task_oid)} 条。")

    # ---- Step 3：汇总所有 task_oid，批量查询 GBase ----
    all_task_oids = list(set(id_to_task_oid.values()))
    print(f"共 {len(all_task_oids)} 个唯一 task_oid，开始查询 GBase...")

    gbase_records = query_gbase_by_task_oids(all_task_oids)
    gbase_map = {str(r["TASK_OID"]): r for r in gbase_records}
    print(f"GBase 共返回 {len(gbase_records)} 条记录。")

    # ---- Step 4：构建输出行：每个 id 对应的 task_oid 单独一行 ----
    output_rows = []
    for _, row in df_input.iterrows():
        ids_in_row = parse_matched_issue_ids(row.get("matched_issue_ids", ""))
        for id_val in ids_in_row:
            task_oid = id_to_task_oid.get(str(id_val), "")
            gbase_info = gbase_map.get(str(task_oid), {}) if task_oid else {}
            output_rows.append({
                "task_oid": task_oid,
                "报障标题": gbase_info.get("TASK_TITLE", ""),
                "报障内容": gbase_info.get("fld_faultcontent", ""),
                "分公司处理意见": gbase_info.get("fld_column_2", ""),
            })

    if not output_rows:
        print("未生成任何输出行，请检查 matched_issue_ids 列内容。")
        return

    # ---- Step 5：保存结果到新 xlsx ----
    df_output = pd.DataFrame(output_rows)

    base, ext = os.path.splitext(input_xlsx)
    output_dir = os.path.dirname(input_xlsx)
    os.makedirs(output_dir, exist_ok=True)
    output_xlsx = os.path.join(output_dir, f"{os.path.basename(base)}_gbase_result{ext}")
    df_output.to_excel(output_xlsx, index=False)

    print(f"\n✅ 完成！共输出 {len(df_output)} 行，已保存到：{output_xlsx}")


if __name__ == "__main__":
    # 修改此处为实际的输入 xlsx 文件路径
    input_file = r"./data/包月费问题.xlsx"
    main(input_file)
