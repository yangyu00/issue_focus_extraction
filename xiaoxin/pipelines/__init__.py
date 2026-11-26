"""业务编排层：把 api / extraction / llm / db 串成两条流水线。

- issue_pipeline：每日/批量工单解析入库
- qa_pipeline：按时间段生成 QA 知识
"""
