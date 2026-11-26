#!/usr/bin/env python3
"""上线前只读连通性自检。

检查 5 项：app 入库 MySQL、QA MySQL、GBase、后端取数接口、直连模型服务。
不写入任何业务数据；模型项会发起 1 次极短的 chat/completions 调用。

    python -m xiaoxin_work_order.cli.verify_env
    python -m xiaoxin_work_order.cli.verify_env --skip-model --skip-api
"""
import argparse
import datetime
import sys
import time

import pymysql
import requests

from xiaoxin_work_order.config import (
    BACKEND_API_URL,
    GBASE_CONFIG,
    LLM_URL,
    MYSQL_CONFIG,
    QA_MYSQL_CONFIG,
)
from xiaoxin_work_order.db import APP_TABLES

# 只读检查：不打印任何账号、密码、API Key


def _check_mysql(cfg, label, tables=()):
    t0 = time.time()
    conn = pymysql.connect(
        connect_timeout=5, read_timeout=5, write_timeout=5, **cfg
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION()")
            version = cur.fetchone()[0]
        note = f"server {version}"
        if tables:
            placeholders = ",".join(["%s"] * len(tables))
            cur = conn.cursor()
            cur.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                f"WHERE TABLE_SCHEMA=%s AND TABLE_NAME IN ({placeholders})",
                (cfg.get("database") or cfg.get("db"), *tables),
            )
            found = {row[0] for row in cur.fetchall()}
            missing = [t for t in tables if t not in found]
            cur.close()
            if missing:
                note += f" | WARN 缺少表: {','.join(missing)}"
        return True, note, time.time() - t0
    finally:
        conn.close()


def _check_api():
    t0 = time.time()
    yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    params = {
        "startTime": f"{yesterday} 00:00:00",
        "endTime": f"{yesterday} 23:59:59",
    }
    resp = requests.get(BACKEND_API_URL, params=params, timeout=10)
    elapsed = time.time() - t0
    try:
        data = resp.json()
    except Exception:
        return False, f"HTTP {resp.status_code}，返回非 JSON: {resp.text[:80]}", elapsed
    if resp.status_code != 200 or data.get("code") != 200 or not data.get("success"):
        return False, f"HTTP {resp.status_code}，业务 code={data.get('code')}，msg={data.get('message')}", elapsed
    count = len(data.get("result") or [])
    return True, f"HTTP 200，昨日返回 {count} 条", elapsed


def _check_model():
    t0 = time.time()
    from xiaoxin_work_order.llm.client import chat

    text = chat("只回复两个字：OK", max_retries=1, timeout=30)
    return True, f"返回 {len(text)} 字: {text[:20]}", time.time() - t0


def main():
    parser = argparse.ArgumentParser(
        prog="python -m xiaoxin_work_order.cli.verify_env",
        description="上线前只读连通性自检",
    )
    parser.add_argument("--skip-api", action="store_true", help="跳过后端取数接口检查")
    parser.add_argument("--skip-model", action="store_true", help="跳过模型服务检查（不消耗调用）")
    args = parser.parse_args()

    checks = [
        ("app 入库 MySQL", lambda: _check_mysql(MYSQL_CONFIG, "app", APP_TABLES)),
        ("QA MySQL", lambda: _check_mysql(QA_MYSQL_CONFIG, "qa")),
        ("GBase opera", lambda: _check_mysql(GBASE_CONFIG, "gbase")),
    ]
    if not args.skip_api:
        checks.append(("后端取数接口", _check_api))
    if not args.skip_model:
        checks.append(("直连模型服务", _check_model))

    print(f"目标模型服务: {LLM_URL}")
    print(f"目标取数接口: {BACKEND_API_URL}")
    print("-" * 72)

    failed = 0
    for label, fn in checks:
        try:
            ok, note, elapsed = fn()
        except Exception as e:
            ok, note, elapsed = False, f"{type(e).__name__}: {str(e)[:160]}", 0.0
        status = "PASS" if ok else "FAIL"
        if not ok:
            failed += 1
        print(f"[{status}] {label:<16} {elapsed:6.2f}s  {note}")

    print("-" * 72)
    print(f"汇总：{len(checks)} 项，通过 {len(checks) - failed}，失败 {failed}")
    print("说明：本脚本只做 SELECT / 查询，不写入任何业务表；WARN 表示连通但表结构待确认。")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
