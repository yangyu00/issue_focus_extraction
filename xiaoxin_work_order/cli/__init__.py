"""命令行入口：参数解析、每日调度、上线自检、公共校验与辅助命令。

这里是唯一的入口层：

    python -m xiaoxin_work_order.cli.app           # 每日调度 / 批量补跑 / 单日
    python -m xiaoxin_work_order.cli.generate_qa   # QA 知识生成 / Excel 反查
    python -m xiaoxin_work_order.cli.verify_env    # 上线前只读连通性自检

validators.py 收敛日期校验，qa_excel.py 承载手动 Excel 反查命令；
日志由各入口的 main() 统一初始化一次。
"""
