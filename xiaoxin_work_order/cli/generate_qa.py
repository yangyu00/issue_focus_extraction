#!/usr/bin/env python3
"""QA 生成的命令行入口。

业务逻辑见 xiaoxin_work_order/pipelines/qa_pipeline.py；
``--enrich-excel`` 辅助命令见 xiaoxin_work_order/cli/qa_excel.py。
日志只在这里初始化一次（用 QA 专用日志文件名）。
"""
import argparse

from xiaoxin_work_order.cli.qa_excel import enrich_qa_excel
from xiaoxin_work_order.cli.validators import valid_date
from xiaoxin_work_order.config import GENERATE_QA_ERROR_LOG_NAME, GENERATE_QA_INFO_LOG_NAME
from xiaoxin_work_order.logging_setup import setup_logging
from xiaoxin_work_order.pipelines.qa_pipeline import process_non_vendor_data


def main():
    setup_logging(GENERATE_QA_INFO_LOG_NAME, GENERATE_QA_ERROR_LOG_NAME)

    parser = argparse.ArgumentParser(
        prog="python -m xiaoxin_work_order.cli.generate_qa",
        description="按时间段生成 QA 知识",
    )
    parser.add_argument("--start", type=valid_date, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", type=valid_date, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--qa-excel", action="store_true", help="额外把 QA 结果导出为 Excel")
    parser.add_argument("--enrich-excel", metavar="XLSX",
                        help="手动模式：读取 QA Excel 的 matched_issue_ids 反查 GBase 明细")
    args = parser.parse_args()

    if args.enrich_excel:
        enrich_qa_excel(args.enrich_excel)
        return
    if not args.start or not args.end:
        parser.error("需要同时提供 --start 和 --end（或用 --enrich-excel 走辅助模式）")
    if args.start > args.end:
        parser.error("--start 不能晚于 --end")
    process_non_vendor_data(args.start, args.end, save_qa_excel=args.qa_excel)


if __name__ == "__main__":
    main()
