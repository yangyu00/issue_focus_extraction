#!/usr/bin/env python3
"""调度 / 命令行入口。

业务逻辑见 xiaoxin/pipelines/issue_pipeline.py。
"""
import argparse
import datetime
import time

import schedule

from xiaoxin.config import SCHEDULE_TIME
from xiaoxin.logging_setup import logger
from xiaoxin.pipelines.issue_pipeline import (
    run_batch_job,
    run_daily_job,
    run_process_for_date,
)


def _valid_date(value):
    try:
        datetime.datetime.strptime(value, "%Y-%m-%d")
        return value
    except ValueError:
        raise argparse.ArgumentTypeError(f"日期格式应为 YYYY-MM-DD: {value}")


def main():
    parser = argparse.ArgumentParser(
        prog="python -m xiaoxin.cli.app",
        description="工单解析入库（每日调度 / 批量补跑 / 单日）",
    )
    parser.add_argument(
        "--mode", choices=["schedule", "daily", "date", "batch"], default="schedule",
        help="schedule=立即跑昨天+每日定时(默认)；daily=只跑昨天；date=跑指定单日；batch=跑日期区间",
    )
    parser.add_argument("--date", type=_valid_date, help="--mode date 的日期 YYYY-MM-DD")
    parser.add_argument("--start", type=_valid_date, help="--mode batch 的起始日期 YYYY-MM-DD")
    parser.add_argument("--end", type=_valid_date, help="--mode batch 的结束日期 YYYY-MM-DD")
    parser.add_argument("--at", default=SCHEDULE_TIME, help="schedule 模式的每日执行时间，默认 01:00")
    parser.add_argument("--no-run-now", action="store_true", help="schedule 模式启动时跳过「立即跑昨天」")
    args = parser.parse_args()

    if args.mode == "batch":
        if not args.start or not args.end:
            parser.error("--mode batch 需要同时提供 --start 和 --end")
        if args.start > args.end:
            parser.error("--start 不能晚于 --end")
        run_batch_job(args.start, args.end)
        return

    if args.mode == "date":
        if not args.date:
            parser.error("--mode date 需要提供 --date")
        run_process_for_date(args.date)
        return

    if args.mode == "daily":
        run_daily_job()
        return

    # schedule：启动时跑昨天，随后按 --at 每日调度
    print("=== 服务已启动，等待定时任务触发 ===")
    if not args.no_run_now:
        run_daily_job()
    schedule.every().day.at(args.at).do(run_daily_job)
    while True:
        try:
            schedule.run_pending()
            time.sleep(60)
        except Exception as e:
            logger.error(f"调度器主循环异常：{e}")
            time.sleep(60)


if __name__ == "__main__":
    main()
