#!/usr/bin/env python3
"""调度 / 命令行入口。

业务逻辑见 xiaoxin_work_order/pipelines/issue_pipeline.py。
就绪探测与当日重试状态机见 xiaoxin_work_order/scheduling.py。

日志只在这里初始化一次；流水线模块只导入 logger，不各自配置。
"""
import argparse
import datetime
import time

import schedule

from xiaoxin_work_order.cli.validators import valid_date
from xiaoxin_work_order.config import (
    ERROR_LOG_NAME,
    INFO_LOG_NAME,
    MAX_RETRY_ATTEMPTS,
    RETRY_INTERVAL_HOURS,
    RETRY_WINDOW_END,
    SCHEDULE_TIME,
    SCHEDULER_POLL_SECONDS,
    STABILITY_RECHECK_MINUTES,
)
from xiaoxin_work_order.logging_setup import logger, setup_logging
from xiaoxin_work_order.pipelines.issue_pipeline import (
    fetch_for_date,
    run_batch_job,
    run_daily_job,
    run_process_for_date,
)
from xiaoxin_work_order.scheduling import DailyRetryQueue, Event, EventKind, RetryPolicy


def _log_event(ev: Event):
    """屏幕与文件都来自 logger 的 stderr / 文件 sink，不再 print 双发。"""
    line = f"[调度] {ev.date_str} {ev.kind}: {ev.message}"
    if ev.kind in (EventKind.ABANDONED, EventKind.INVALID):
        logger.warning(line)
    else:
        logger.info(line)


def _yesterday_str() -> str:
    return (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")


def main():
    setup_logging(INFO_LOG_NAME, ERROR_LOG_NAME)

    parser = argparse.ArgumentParser(
        prog="python -m xiaoxin_work_order.cli.app",
        description="工单解析入库（每日调度 / 批量补跑 / 单日）",
    )
    parser.add_argument(
        "--mode", choices=["schedule", "daily", "date", "batch"], default="schedule",
        help="schedule=每日就绪探测+当日重试(默认)；daily=只跑昨天；date=跑指定单日；batch=跑日期区间",
    )
    parser.add_argument("--date", type=valid_date, help="--mode date 的日期 YYYY-MM-DD")
    parser.add_argument("--start", type=valid_date, help="--mode batch 的起始日期 YYYY-MM-DD")
    parser.add_argument("--end", type=valid_date, help="--mode batch 的结束日期 YYYY-MM-DD")
    parser.add_argument("--at", default=SCHEDULE_TIME, help="schedule 模式的每日触发时间，默认 01:00")
    parser.add_argument(
        "--no-run-now", action="store_true",
        help="schedule 模式启动时不登记任何批次，纯等到每日触发时刻",
    )
    parser.add_argument(
        "--retry-interval-hours", type=float, default=RETRY_INTERVAL_HOURS,
        help="空数据 / 未稳定后的重试间隔（小时），默认 1",
    )
    parser.add_argument(
        "--stability-recheck-minutes", type=float, default=STABILITY_RECHECK_MINUTES,
        help="首次拿到非空数据后的快速复核间隔（分钟），默认 10；设 0 则统一按小时重试",
    )
    parser.add_argument(
        "--retry-window-end", default=RETRY_WINDOW_END,
        help="当天最晚发起探测的时刻 HH:MM，默认 23:00；跨过即放弃且不带到转天",
    )
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
        # 手动单次：立刻取昨天并入库，不走就绪探测
        run_daily_job()
        return

    # ---------------- schedule：就绪探测 + 当日重试 ----------------
    policy = RetryPolicy(
        interval_hours=args.retry_interval_hours,
        recheck_minutes=args.stability_recheck_minutes,
        window_end=args.retry_window_end,
        max_attempts=MAX_RETRY_ATTEMPTS,
    )
    try:
        policy.end_time()
    except Exception:
        parser.error("--retry-window-end 需为 HH:MM，例如 23:00")
    if args.retry_window_end <= args.at:
        parser.error("--retry-window-end 必须晚于 --at，否则当日批次会被立刻放弃")

    queue = DailyRetryQueue(policy)

    def register_yesterday():
        """01:00 触发点只做登记，不在这里执行任务。"""
        for ev in queue.register(_yesterday_str(), datetime.datetime.now()):
            _log_event(ev)

    def run_stable(date_str, df):
        run_process_for_date(date_str, df=df)

    schedule.every().day.at(args.at).do(register_yesterday)
    if not args.no_run_now:
        register_yesterday()

    print(
        f"=== 服务已启动，等待每日 {args.at} 触发；就绪探测间隔 "
        f"{args.retry_interval_hours:g}h / 快速复核 {args.stability_recheck_minutes:g}min / "
        f"当天截止 {args.retry_window_end} ===",
        flush=True,
    )
    while True:
        try:
            schedule.run_pending()
            for ev in queue.tick(
                datetime.datetime.now(), fetch=fetch_for_date, on_stable=run_stable,
            ):
                _log_event(ev)
            time.sleep(SCHEDULER_POLL_SECONDS)
        except Exception as e:
            logger.error(f"调度器主循环异常：{e}")
            time.sleep(SCHEDULER_POLL_SECONDS)


if __name__ == "__main__":
    main()
