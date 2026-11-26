"""日志配置。"""
from loguru import logger

from xiaoxin_work_order.config import (
    ERROR_LOG_NAME,
    ERROR_LOG_PATH,
    INFO_LOG_NAME,
    INFO_LOG_PATH,
)


def setup_logging(info_log_name=INFO_LOG_NAME, error_log_name=ERROR_LOG_NAME):
    # 移除默认配置，重新配置
    logger.remove()

    # INFO 日志
    logger.add(
        f"{INFO_LOG_PATH}/{info_log_name}_{{time:YYYY-MM-DD}}.log",
        level="INFO",
        rotation="00:00",       # 每天生成一个新日志文件
        encoding="utf8",
        compression=None,       # 不压缩
        retention="180 days",   # 保留180天
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | (file:{file.name}:{line}) {message}",
        backtrace=True,
        diagnose=True,
    )

    # ERROR 日志
    logger.add(
        f"{ERROR_LOG_PATH}/{error_log_name}_{{time:YYYY-MM-DD}}.log",
        level="ERROR",
        rotation="00:00",
        encoding="utf8",
        compression=None,
        retention="180 days",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | (file:{file.name}:{line}) {message}",
        backtrace=True,
        diagnose=True,
    )


# 默认初始化一次，保证其他引用的地方能正常使用默认配置
setup_logging()
