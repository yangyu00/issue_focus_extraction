"""模型侧能力：客户端 / 提示词加载 / 分类映射表。

各模块按需从具体子模块导入（例如 `from xiaoxin_work_order.llm.mappings import MAPPING`），
本文件不 eager import 任何子模块，以保证 `tests/check_prompts.py` 的零依赖基线校验可用。
"""
