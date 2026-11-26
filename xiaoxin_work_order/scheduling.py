"""每日批次的就绪探测与「当日重试」状态机（纯逻辑，不依赖外部服务）。

背景：上游库有时还没准备好昨天的数据（接口返回空），有时正在补写
（按日期查只拿到不完整的快照）。因此不能直接拿第一次结果就跑模型入库。

约定：
- 每次探测只取数、不跑模型、不写库；连续两次「指纹」一致才认为数据稳定。
- 指纹 = 行数 + taskOid 集合 + 最大 lastTime，能识别「删一条补一条、总数不变」。
- 所有等待都表达为 next_at，由主循环轮询推进，绝不 sleep 阻塞调度器。
- 重试严格限制在「登记时的运行日」当天，跨天立即丢弃，不带到转天。
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Fingerprint:
    count: int
    oids: tuple[str, ...]
    last_time: str
    valid: bool  # 是否含 taskOid 列（缺列说明接口返回异常，不能入库）


def data_fingerprint(df) -> Fingerprint:
    """从取数结果算稳定性指纹；只比行数会漏掉主键集合变化的情况。"""
    if df is None or getattr(df, "empty", True):
        return Fingerprint(0, (), "", True)
    cols = set(getattr(df, "columns", []))
    if "taskOid" not in cols:
        return Fingerprint(len(df), (), "", False)
    oids = tuple(sorted(str(x) for x in df["taskOid"].tolist()))
    last_time = ""
    if "lastTime" in cols:
        vals = [
            str(x) for x in df["lastTime"].tolist()
            if x is not None and str(x) not in ("nan", "None", "")
        ]
        if vals:
            last_time = max(vals)
    return Fingerprint(len(df), oids, last_time, True)


@dataclass
class RetryPolicy:
    interval_hours: float = 1.0        # 空 / 未稳定后的重试间隔
    recheck_minutes: float = 10.0      # 首次拿到非空数据后的快速复核间隔；设 0 则统一按小时重试
    window_end: str = "23:00"          # 当天最晚发起探测的时刻 HH:MM
    max_attempts: int = 24             # 单个日期批次的探测次数上限

    def end_time(self) -> datetime.time:
        hh, mm = self.window_end.split(":")
        return datetime.time(int(hh), int(mm))


@dataclass(frozen=True)
class Event:
    kind: str        # registered / duplicate / already_done / empty / invalid /
                     # first_load / growing / stable / abandoned
    date_str: str
    message: str
    next_at: datetime.datetime | None = None


@dataclass
class _Task:
    date_str: str
    run_day: datetime.date
    next_at: datetime.datetime
    attempts: int = 0
    last_fp: Fingerprint | None = None
    fast_recheck_used: bool = False


class DailyRetryQueue:
    """由主循环驱动；fetch / run 由外部注入，便于离线测试与复用。

    fetch(date_str) -> DataFrame：只取数。
    run(date_str, df)：用已确认稳定的 df 跑提取与入库。
    """

    def __init__(self, policy: RetryPolicy):
        self.policy = policy
        self._pending: dict[str, _Task] = {}
        self._completed: dict[str, datetime.date] = {}

    @property
    def pending_dates(self) -> list[str]:
        return sorted(self._pending)

    def register(self, date_str: str, now: datetime.datetime) -> list[Event]:
        """登记一个待处理日期；同一日期重复登记幂等，当天已入库不再重跑。"""
        if date_str in self._pending:
            return [Event("duplicate", date_str, "该日期已在重试队列中，跳过重复登记")]
        if self._completed.get(date_str) == now.date():
            return [Event("already_done", date_str, "该日期当天已成功入库，跳过，避免重复跑模型")]
        self._pending[date_str] = _Task(date_str, now.date(), now)
        return [Event("registered", date_str, f"登记当日待处理批次，运行日 {now.date()}")]

    def tick(
        self,
        now: datetime.datetime,
        fetch: Callable[[str], object],
        run: Callable[[str, object], None],
    ) -> list[Event]:
        """推进一次；返回本次产生的事件，供调用方打日志。"""
        for key in [k for k, day in self._completed.items() if day != now.date()]:
            del self._completed[key]

        events: list[Event] = []
        end_at = self.policy.end_time()
        for date_str, task in list(self._pending.items()):
            # —— 三条硬边界：跨天 / 超过当天窗口 / 次数用尽，一律不带到转天 ——
            if now.date() != task.run_day:
                events.append(self._abandon(date_str, "已跨天，放弃该批次"))
                continue
            if now.time() >= end_at:
                events.append(self._abandon(date_str, f"超过当天重试窗口 {self.policy.window_end}，放弃"))
                continue
            if task.attempts >= self.policy.max_attempts:
                events.append(self._abandon(date_str, f"达到最大探测次数 {self.policy.max_attempts}，放弃"))
                continue
            if now < task.next_at:
                continue

            df = fetch(date_str)
            fp = data_fingerprint(df)
            task.attempts += 1
            n = task.attempts

            if fp.count == 0:
                task.last_fp = None
                task.fast_recheck_used = False
                task.next_at = now + datetime.timedelta(hours=self.policy.interval_hours)
                events.append(Event(
                    "empty", date_str,
                    f"第 {n} 次探测：上游返回空，{self.policy.interval_hours}h 后重试",
                    task.next_at,
                ))
            elif not fp.valid:
                task.last_fp = None
                task.fast_recheck_used = False
                task.next_at = now + datetime.timedelta(hours=self.policy.interval_hours)
                events.append(Event(
                    "invalid", date_str,
                    f"第 {n} 次探测：返回缺少 taskOid 列，暂不入库，{self.policy.interval_hours}h 后重试",
                    task.next_at,
                ))
            elif task.last_fp is not None and fp == task.last_fp:
                # 连续两次指纹一致 → 数据不再增长，用「这次」的 df 入库，不再二次取数
                run(date_str, df)
                del self._pending[date_str]
                self._completed[date_str] = now.date()
                events.append(Event(
                    "stable", date_str,
                    f"第 {n} 次探测：与上次指纹一致（{fp.count} 条），数据已稳定，开始入库",
                ))
            else:
                grew = task.last_fp is not None and fp.count > task.last_fp.count
                task.last_fp = fp
                if not task.fast_recheck_used and self.policy.recheck_minutes > 0:
                    task.fast_recheck_used = True
                    task.next_at = now + datetime.timedelta(minutes=self.policy.recheck_minutes)
                    events.append(Event(
                        "first_load", date_str,
                        f"第 {n} 次探测：拿到 {fp.count} 条，{self.policy.recheck_minutes:g}min 后复核是否仍在增长",
                        task.next_at,
                    ))
                else:
                    task.next_at = now + datetime.timedelta(hours=self.policy.interval_hours)
                    detail = "记录数仍在增长" if grew else "指纹发生变化"
                    events.append(Event(
                        "growing", date_str,
                        f"第 {n} 次探测：{detail}（当前 {fp.count} 条），数据未稳定，"
                        f"{self.policy.interval_hours:g}h 后再校验",
                        task.next_at,
                    ))
        return events

    def _abandon(self, date_str: str, reason: str) -> Event:
        del self._pending[date_str]
        return Event(
            "abandoned", date_str,
            reason + "；该日期当天未就绪，如需补数据请手动 "
                     "--mode batch --start <日期> --end <日期>",
        )
