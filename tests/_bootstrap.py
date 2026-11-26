"""tests/ 下各脚本共用的路径引导（零第三方依赖）。

把 tests/ 目录与仓库根都加入 sys.path，之后各脚本可安全导入 xiaoxin_work_order。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

for path in (HERE, ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)
