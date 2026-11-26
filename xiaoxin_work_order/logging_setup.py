"""日志配置：只在 CLI 入口调用一次 ``setup_logging()``，库模块只导入 ``logger``。

三个 sink：INFO 文件、ERROR 文件、stderr（屏幕）。业务事件统一走 logger，
不再用 ``print`` 与 ``logger`` 双发；只有交互式横幅 / CLI 报告保留 print。
loguru 的 logger 是全局单例，流水线模块若各自配置会互相覆盖，故收口到入口。
"""
import os
import sys

from loguru import logger

from xiaoxin_work_order.config import (
    ERROR_LOG_NAME,
    ERROR_LOG_PATH,
    INFO_LOG_NAME,
    INFO_LOG_PATH,
)

_FILE_FORMAT = "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | (file:{file.name}:{line}) {message}"
_STDERR_FORMAT = "{time:HH:mm:ss} | {level} | {message}"
_ROTATION = "00:00"        # 每天生成一个新日志文件
_RETENTION = "180 days"


def _add_daily_sink(path, name, level):
    """按天轮转的文件 sink；INFO / ERROR 两份只有路径与 level 不同。"""
    logger.add(
        f"{path}/{name}_{{time:YYYY-MM-DD}}.log",
        level=level,
        rotation=_ROTATION,
        encoding="utf8",
        compression=None,
        retention=_RETENTION,
        format=_FILE_FORMAT,
        backtrace=True,
        diagnose=True,
    )


def setup_logging(info_log_name=INFO_LOG_NAME, error_log_name=ERROR_LOG_NAME):
    """配置全局 logger（重复调用会整体重置）。

    日志目录的创建也收口在这里：import 模块时既不连库、也不建目录。
    """
    os.makedirs(INFO_LOG_PATH, exist_ok=True)
    os.makedirs(ERROR_LOG_PATH, exist_ok=True)

    logger.remove()
    _add_daily_sink(INFO_LOG_PATH, info_log_name, "INFO")
    _add_daily_sink(ERROR_LOG_PATH, error_log_name, "ERROR")
    logger.add(sys.stderr, level="INFO", format=_STDERR_FORMAT)
