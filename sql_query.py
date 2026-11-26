import pandas as pd
from sqlalchemy import create_engine

# ================ 配置区域 ================
# 这里的数据库配置与您项目中保持一致
DB_CONFIG = {
    "host": "134.84.22.233",
    "port": 6306,
    "user": "root",
    "password": "Nntslgkpt*2024",
    "database": "ntt_xiaoxin_agent",
    "charset": "utf8mb4"
}

# 数据库连接 URL
db_url = f"mysql+pymysql://{DB_CONFIG['user']}:{DB_CONFIG['password']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}?charset={DB_CONFIG['charset']}"
engine = create_engine(db_url, pool_recycle=3600, pool_pre_ping=True)


def get_records_by_ids(id_list):
    """
    根据给定的 id 列表查询 xxa_bomc_qa_non_vendor 表数据
    :param id_list: list, 包含要查询的 id (例如 [123, 234])
    :return: pandas.DataFrame, 查询到的数据结果
    """
    if not id_list:
        print("传入的 id 列表为空，直接返回空 DataFrame。")
        return pd.DataFrame()

    table_name = "xxa_bomc_qa_non_vendor"

    # 为了防止 SQL 注入并且兼容任意长度的列表，这里使用参数化查询
    # 根据列表长度生成对应数量的 %s 占位符 (例如两个元素就会生成 "%s, %s")
    placeholders = ",".join(["%s"] * len(id_list))
    sql = f"SELECT * FROM {table_name} WHERE id IN ({placeholders})"

    try:
        # 使用 pandas 的 read_sql 方法执行查询，将 id_list 转化为元组传入占位符
        df = pd.read_sql(sql, engine, params=tuple(id_list))
        return df
    except Exception as e:
        print(f"执行数据库查询时发生错误: {e}")
        return pd.DataFrame()
