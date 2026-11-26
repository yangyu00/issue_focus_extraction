"""``--enrich-excel`` 手动辅助命令：从 QA Excel 反查 GBase 工单明细。

id 解析统一走 common.parse_issue_ids()。
"""
import os

import pandas as pd

from xiaoxin_work_order.common import parse_issue_ids
from xiaoxin_work_order.db import (
    NON_VENDOR_TABLE,
    get_records_by_ids,
    query_gbase_by_task_oids,
)
from xiaoxin_work_order.logging_setup import logger


def enrich_qa_excel(input_xlsx: str):
    """读 QA Excel 的 matched_issue_ids → 反查 task_oid → 查 GBase → 另存明细 Excel。"""
    if not os.path.exists(input_xlsx):
        logger.error(f"文件不存在: {input_xlsx}")
        return
    df_input = pd.read_excel(input_xlsx)
    if "matched_issue_ids" not in df_input.columns:
        logger.error(f"文件中缺少 matched_issue_ids 列，现有列：{df_input.columns.tolist()}")
        return

    all_ids = set()
    for val in df_input["matched_issue_ids"]:
        all_ids.update(parse_issue_ids(val))
    id_list = [int(i) for i in all_ids if str(i).strip().isdigit()]
    logger.info(f"共解析出 {len(id_list)} 个唯一 id，查询 {NON_VENDOR_TABLE} 获取 task_oid...")

    non_vendor_df = get_records_by_ids(id_list)
    if non_vendor_df.empty or "task_oid" not in non_vendor_df.columns:
        logger.error("未查到对应记录或缺少 task_oid 列，终止。")
        return

    id_to_task_oid = {
        str(row["id"]): str(row["task_oid"])
        for _, row in non_vendor_df.iterrows()
        if pd.notna(row.get("task_oid")) and pd.notna(row.get("id"))
    }
    all_task_oids = list(set(id_to_task_oid.values()))
    gbase_records = query_gbase_by_task_oids(all_task_oids)
    gbase_map = {str(r.get("TASK_OID", "")): r for r in gbase_records}

    output_rows = []
    for _, row in df_input.iterrows():
        for id_val in parse_issue_ids(row.get("matched_issue_ids", "")):
            task_oid = id_to_task_oid.get(str(id_val), "")
            info = gbase_map.get(str(task_oid), {}) if task_oid else {}
            output_rows.append({
                "task_oid": task_oid,
                "报障标题": info.get("TASK_TITLE", ""),
                "报障内容": info.get("fld_faultcontent", ""),
                "分公司处理意见": info.get("fld_column_2", ""),
            })
    if not output_rows:
        logger.warning("未生成任何输出行，请检查 matched_issue_ids 列内容。")
        return

    base, ext = os.path.splitext(input_xlsx)
    output_dir = os.path.dirname(input_xlsx) or "."
    os.makedirs(output_dir, exist_ok=True)
    output_xlsx = os.path.join(output_dir, f"{os.path.basename(base)}_gbase_result{ext}")
    pd.DataFrame(output_rows).to_excel(output_xlsx, index=False)
    logger.info(f"✅ 完成！共输出 {len(output_rows)} 行，已保存到：{output_xlsx}")
