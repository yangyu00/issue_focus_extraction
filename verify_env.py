#!/usr/bin/env python3
"""兼容入口：保持 `python verify_env.py` 的调用方式不变。

实际实现位于 xiaoxin/cli/verify_env.py。
"""
import sys

from xiaoxin.cli.verify_env import main

if __name__ == "__main__":
    sys.exit(main())
