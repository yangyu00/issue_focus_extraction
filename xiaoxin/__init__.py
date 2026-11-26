"""小信智能体摘要提取 —— 工单解析入库 + QA 知识生成。

分层：
    config / logging_setup / common   配置、日志、纯函数
    api                              后端取数接口（HTTP，只读）
    llm                              模型客户端、提示词加载、分类映射表
    extraction                       8 字段提取工作流编排
    db                               三套数据源的全部读写（详见 xiaoxin/db/README.md）
    pipelines                        业务编排：取数→提取→入库、聚类→写库
    cli                              命令行入口、参数解析与每日调度

根目录的 app.py / generate_qa.py / verify_env.py 只是兼容旧命令的薄入口。
"""
__version__ = "1.0.0"
