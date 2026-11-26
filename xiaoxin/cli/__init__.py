"""命令行入口：参数解析、每日调度、上线自检。

这里是唯一的入口层：

    python -m xiaoxin.cli.app           # 每日调度 / 批量补跑 / 单日
    python -m xiaoxin.cli.generate_qa   # QA 知识生成
    python -m xiaoxin.cli.verify_env    # 上线前只读连通性自检
"""
