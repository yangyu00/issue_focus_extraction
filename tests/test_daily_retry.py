#!/usr/bin/env python3
"""每日就绪探测 / 当日重试状态机的离线回归测试。

不访问任何接口、数据库或模型；fetch / run / now 全部注入。

    python tests/test_daily_retry.py
"""
import datetime
import os
import sys
import unittest

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from xiaoxin_work_order.scheduling import (  # noqa: E402
    DailyRetryQueue,
    RetryPolicy,
    data_fingerprint,
)

T0 = datetime.datetime(2026, 11, 26, 1, 0, 0)
DATE = "2026-11-25"


def frame(oids, last="2026-11-25 10:00:00"):
    return pd.DataFrame({"taskOid": oids, "lastTime": [last] * len(oids)})


EMPTY = pd.DataFrame()
A = frame(["T1", "T2", "T3"])
B = frame(["T1", "T2", "T3", "T4", "T5"])
SAME_COUNT_DIFF = frame(["T1", "T2", "T4"])


class SeqFetch:
    """按序返回预置结果；用尽后重复最后一个，便于模拟「一直为空」。"""

    def __init__(self, *frames):
        self.frames = list(frames) or [EMPTY]
        self.calls = 0

    def __call__(self, date_str):
        f = self.frames[min(self.calls, len(self.frames) - 1)]
        self.calls += 1
        return f


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, date_str, df):
        self.calls.append((date_str, len(df)))


def kinds(events):
    return [e.kind for e in events]


class TestDailyRetry(unittest.TestCase):
    def setUp(self):
        self.q = DailyRetryQueue(RetryPolicy())
        self.q.register(DATE, T0)

    def test_empty_then_recheck_then_stable(self):
        """空 → 隔 1 小时重试 → 拿到数据 → 10 分钟复核一致 → 才入库。"""
        fetch, run = SeqFetch(EMPTY, A, A), Recorder()
        e0 = self.q.tick(T0, fetch, run)
        self.assertEqual(kinds(e0), ["empty"])
        self.assertEqual(e0[0].next_at, T0 + datetime.timedelta(hours=1))
        self.assertEqual(run.calls, [])

        # 不到点不探测
        self.assertEqual(self.q.tick(T0 + datetime.timedelta(minutes=59), fetch, run), [])
        self.assertEqual(fetch.calls, 1)

        e1 = self.q.tick(T0 + datetime.timedelta(hours=1), fetch, run)
        self.assertEqual(kinds(e1), ["first_load"])
        self.assertEqual(e1[0].next_at, T0 + datetime.timedelta(hours=1, minutes=10))
        self.assertEqual(run.calls, [])

        e2 = self.q.tick(T0 + datetime.timedelta(hours=1, minutes=10), fetch, run)
        self.assertEqual(kinds(e2), ["stable"])
        self.assertEqual(run.calls, [(DATE, 3)])
        self.assertEqual(self.q.pending_dates, [])

    def test_growing_data_waits_until_stable(self):
        """上游仍在补写：记录数增长时不入库，直到连续两次一致。"""
        fetch, run = SeqFetch(A, B, B), Recorder()
        self.assertEqual(kinds(self.q.tick(T0, fetch, run)), ["first_load"])
        e1 = self.q.tick(T0 + datetime.timedelta(minutes=10), fetch, run)
        self.assertEqual(kinds(e1), ["growing"])
        self.assertIn("增长", e1[0].message)
        self.assertEqual(run.calls, [])

        t2 = T0 + datetime.timedelta(hours=1, minutes=10)
        self.assertEqual(kinds(self.q.tick(t2, fetch, run)), ["stable"])
        self.assertEqual(run.calls, [(DATE, 5)])

    def test_fingerprint_detects_same_count_different_keys(self):
        """删一条补一条、总数不变时也不能判为稳定。"""
        f1 = data_fingerprint(A)
        f2 = data_fingerprint(SAME_COUNT_DIFF)
        self.assertEqual(f1.count, f2.count)
        self.assertNotEqual(f1.oids, f2.oids)
        self.assertNotEqual(f1, f2)

        fetch, run = SeqFetch(A, SAME_COUNT_DIFF, SAME_COUNT_DIFF), Recorder()
        self.assertEqual(kinds(self.q.tick(T0, fetch, run)), ["first_load"])
        self.assertEqual(kinds(self.q.tick(T0 + datetime.timedelta(minutes=10), fetch, run)), ["growing"])
        self.assertEqual(run.calls, [])

    def test_missing_taskoid_never_writes(self):
        """接口返回缺列时不入库，按小时重试。"""
        bad = pd.DataFrame({"foo": [1, 2]})
        fetch, run = SeqFetch(bad), Recorder()
        e = self.q.tick(T0, fetch, run)
        self.assertEqual(kinds(e), ["invalid"])
        self.assertEqual(run.calls, [])
        self.assertEqual(e[0].next_at, T0 + datetime.timedelta(hours=1))

    def test_window_end_abandons_without_affecting_next_day(self):
        """超过当天窗口即丢弃，且丢弃事件里给出手动补跑方式。"""
        fetch, run = SeqFetch(EMPTY), Recorder()
        t = T0
        while t < datetime.datetime(2026, 11, 26, 22, 0):
            t += datetime.timedelta(hours=1)
            self.q.tick(t, fetch, run)
        # 22:00 时仍在按小时重试
        self.assertEqual(self.q.pending_dates, [DATE])
        e = self.q.tick(datetime.datetime(2026, 11, 26, 23, 0), fetch, run)
        self.assertEqual(kinds(e), ["abandoned"])
        self.assertIn("--mode batch", e[0].message)
        self.assertEqual(run.calls, [])
        self.assertEqual(self.q.pending_dates, [])

    def test_cross_day_is_dropped_and_next_day_can_register(self):
        """重试绝不跨天；次日 01:00 的新批次能正常登记。"""
        q = DailyRetryQueue(RetryPolicy(window_end="23:59", max_attempts=100))
        q.register(DATE, T0)
        fetch, run = SeqFetch(EMPTY), Recorder()
        e = q.tick(datetime.datetime(2026, 11, 27, 0, 0), fetch, run)
        self.assertEqual(kinds(e), ["abandoned"])
        self.assertIn("跨天", e[0].message)
        self.assertEqual(q.pending_dates, [])
        self.assertEqual(run.calls, [])

        # 次日 01:00 登记「昨天」= 11-26，是全新批次，不受旧任务影响
        e2 = q.register("2026-11-26", datetime.datetime(2026, 11, 27, 1, 0))
        self.assertEqual(kinds(e2), ["registered"])
        self.assertEqual(q.pending_dates, ["2026-11-26"])

    def test_register_idempotent_and_completed_not_rerun(self):
        """同一天重复登记不打断重试；已入库的日期当天不再跑一遍模型。"""
        self.assertEqual(kinds(self.q.register(DATE, T0)), ["duplicate"])
        fetch, run = SeqFetch(A, A), Recorder()
        self.q.tick(T0, fetch, run)
        self.q.tick(T0 + datetime.timedelta(minutes=10), fetch, run)
        self.assertEqual(run.calls, [(DATE, 3)])
        self.assertEqual(kinds(self.q.register(DATE, T0 + datetime.timedelta(minutes=30))),
                         ["already_done"])

    def test_zero_recheck_uses_hourly_rhythm(self):
        """STABILITY_RECHECK_MINUTES=0 时退化为统一每小时采样。"""
        q = DailyRetryQueue(RetryPolicy(recheck_minutes=0))
        q.register(DATE, T0)
        fetch, run = SeqFetch(A, A), Recorder()
        e0 = q.tick(T0, fetch, run)
        self.assertEqual(kinds(e0), ["growing"])
        self.assertEqual(e0[0].next_at, T0 + datetime.timedelta(hours=1))
        self.assertEqual(kinds(q.tick(T0 + datetime.timedelta(hours=1), fetch, run)), ["stable"])
        self.assertEqual(run.calls, [(DATE, 3)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
