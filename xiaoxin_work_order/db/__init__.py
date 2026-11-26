"""数据库读写统一入口。

三套数据源、5 张表的全部 SQL / 连接 / 字段拼装都收口在本包。
业务层只调用这里的函数，不写 SQL、不建连接。
详细说明与读写内容示例见同目录 README.md。
"""
from xiaoxin_work_order.db.app_records import (
    APP_TABLES,
    TABLE_ISSUES,
    TABLE_NON_VENDOR,
    TABLE_QA_RECORDS,
    TABLE_WORD_CLOUD,
    clean_old_records,
    save_issue_results,
)
from xiaoxin_work_order.db.connections import (
    app_engine,
    gbase_connection,
    qa_connection,
    qa_engine,
)
from xiaoxin_work_order.db.gbase import query_gbase_by_task_oids
from xiaoxin_work_order.db.qa_knowledge import (
    NON_VENDOR_TABLE,
    QA_TABLE,
    get_records_by_ids,
    read_non_vendor,
    save_qa_knowledge,
)
