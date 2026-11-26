"""连接与引擎工厂：全项目唯一创建数据库连接的地方。

业务代码一律通过 xiaoxin.db 的函数读写，不自行 create_engine / connect。
引擎按需创建并缓存（import 时不连库），便于离线测试与本地开发。
"""
from functools import lru_cache

import pymysql
from sqlalchemy import create_engine

from xiaoxin.config import GBASE_CONFIG, MYSQL_CONFIG, QA_MYSQL_CONFIG, mysql_url


@lru_cache(maxsize=None)
def app_engine():
    """app 入库库（MYSQL_CONFIG）的 SQLAlchemy 引擎。"""
    return create_engine(mysql_url(MYSQL_CONFIG), pool_recycle=3600, pool_pre_ping=True)


@lru_cache(maxsize=None)
def qa_engine():
    """QA 读写库（QA_MYSQL_CONFIG）的 SQLAlchemy 引擎。"""
    return create_engine(mysql_url(QA_MYSQL_CONFIG), pool_recycle=3600, pool_pre_ping=True)


def gbase_connection():
    """GBase opera 库的 pymysql 连接（只读明细查询）。"""
    return pymysql.connect(**GBASE_CONFIG)


def qa_connection():
    """QA 库的原生 pymysql 连接（executemany 批量写入用）。"""
    return pymysql.connect(**QA_MYSQL_CONFIG)
