#!/usr/bin/env python3
"""QA 生成的命令行入口（原 generate_qa.py 的 main 部分）。

业务逻辑见 xiaoxin/pipelines/qa_pipeline.py。
"""
import argparse
import datetime

from xiaoxin.pipelines.qa_pipeline import enrich_qa_excel, process_non_vendor_data


def _valid_date(value):
    try:
        datetime.datetime.strptime(value, "%Y-%m-%d")
        return value
    except ValueError:
        raise argparse.ArgumentTypeError(f"日期格式应为 YYYY-MM-DD: {value}")


def main():
    parser = argparse.ArgumentParser(description="按时间段生成 QA 知识")
    parser.add_argument("--start", type=_valid_date, help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", type=_valid_date, help="结束日期 YYYY-MM-DD")
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
