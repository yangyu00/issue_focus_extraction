"""8 个结构化字段的唯一定义（纯常量，零第三方依赖）。

模型输出顺序、入库列顺序、提示词文件名、工作流并发/串行划分全部从这里派生：

- ``FIELDS`` / ``OUTPUT_KEYS``：8 字段及其固定顺序
- ``PROMPT_NAMES``：字段 → ``prompts/<name>.txt`` 的显式映射（默认同名）
- ``DEPENDENT_FIELDS``：依赖上游结果、必须串行调用的节点
- ``PARALLEL_FIELDS``：彼此无依赖、可并发调用的字段

新增/重命名字段时只改本文件，workflow / issue_pipeline / tests 自动跟随。
"""

FIELDS = (
    "device",
    "business",
    "subclass",
    "symptom",
    "symptom_detail",
    "cause",
    "branch_actions",
    "vendor_actions",
)

OUTPUT_KEYS = FIELDS

# 业务 → MAPPING → 二级分类 → MAPPING → 故障现象，必须串行
DEPENDENT_FIELDS = ("subclass", "symptom")

# 其余字段互不依赖，可并发调用
PARALLEL_FIELDS = tuple(f for f in FIELDS if f not in DEPENDENT_FIELDS)

# 字段名默认就是 prompts/ 下的文件名；若某字段改用别的提示词，只改这里
PROMPT_NAMES = {field: field for field in FIELDS}
