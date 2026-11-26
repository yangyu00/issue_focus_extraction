"""CLI 共用的参数校验器（纯函数，只依赖标准库）。

app.py / generate_qa.py 的 ``--date`` ``--start`` ``--end`` 用同一份校验。
"""
import argparse
import datetime


def valid_date(value):
    """校验 YYYY-MM-DD；不符合时抛 argparse.ArgumentTypeError。"""
    try:
        datetime.datetime.strptime(value, "%Y-%m-%d")
        return value
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"日期格式应为 YYYY-MM-DD: {value}") from exc
