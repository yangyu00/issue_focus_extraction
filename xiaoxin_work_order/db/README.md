# `xiaoxin_work_order/db` —— 数据库读写模块

> 一句话：**项目里所有 SQL、连接、表名、字段拼装都收口在这个目录里**。业务层（`pipelines/`）只负责"取数 → 调模型 → 组装 DataFrame → 调本模块的函数"，不再自己写 SQL、不再自己 `create_engine` / `connect`。

---

## 1. 这个模块在业务链路里的位置

```
后端取数接口（HTTP，不在本模块）           prompts/*.txt + 模型服务（不在本模块）
        │  startTime / endTime                       │ 8 字段提取
        ▼                                           ▼
   pipelines/issue_pipeline.py  ─────────────► final_df（一次日期批次）
        │  save_issue_results(final_df)
        ▼
┌────────────────────── app 入库 MySQL（MYSQL_CONFIG）──────────────────────┐
│  xxa_issues_records          焦点问题记录（key_issues = symptom）           │
│  xxa_complaint_word_cloud    投诉词云（key_word = symptom）                 │
│  xxa_bomc_qa_records         有厂商处理意见的全量问答记录                    │
│  xxa_bomc_qa_non_vendor      无厂商处理意见 → QA 聚类的输入                  │
└───────────────────────────────────────────────────────────────────────────┘
                              │ read_non_vendor(起,止) / get_records_by_ids([id...])
                              ▼
                     pipelines/qa_pipeline.py（过滤 → 按 symptom 分组 → 聚类）
                              │                       │
        query_gbase_by_task_oids(task_oids)           │ save_qa_knowledge(qa_data)
                              ▼                       ▼
        GBase opera.xiaoxin（工单原始明细，只读）   QA MySQL（QA_MYSQL_CONFIG）
                                              xxa_city_qa_knowledge 标准问答对
```

对应两句话的业务：

1. **每日/批量入库**：把一条条报障工单经大模型提取成 8 个结构化字段，按「有没有厂商处理意见」分门别类存进 4 张表，供焦点问题、词云、问答检索使用。
2. **QA 知识生成**：从"无厂商处理意见"表里捞出一段时间的数据，补上 GBase 里的工单原文，聚类成标准问答对，写进知识库表。

---

## 2. 三套数据源

| 数据源 | 配置（见 `xiaoxin_work_order/config.py` / `.env`） | 访问方式 | 读/写 | 用途 |
| --- | --- | --- | --- | --- |
| app 入库 MySQL | `MYSQL_CONFIG`（`DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME`） | SQLAlchemy `engine` + `pandas.to_sql` | 只写 | 4 张业务表 |
| QA MySQL | `QA_MYSQL_CONFIG`（`QA_DB_*`） | SQLAlchemy `read_sql` + pymysql `executemany` | 读 + 写 | 读非厂商工单、写标准问答对 |
| GBase | `GBASE_CONFIG`（`GBASE_*`，库 `opera`） | pymysql 原生连接 | 只读 | 工单原始明细 |

> 真实地址、账号、口令不写在本 README；`xiaoxin_work_order/config.py` 为三套数据源与模型服务提供内置默认值，可由 `.env` 或同名环境变量覆盖，优先级：**显式 export > `.env` > `config.py` 内置默认值**。`mysql_url()` 负责生成连接串并对账号密码做 URL 编码；三套配置字典统一用 `database` 键（`GBASE_DB` 只是环境变量名），`verify_env` 不再需要 `db` / `database` 兜底。

**连接在哪创建**：`connections.py` 是唯一入口，`app_engine()` / `qa_engine()` 按需创建并缓存（import 模块时不会连库，方便离线测试），`gbase_connection()` / `qa_connection()` 每次调用新建、用完即关。

---

## 3. 表字典（看这张表就能理解业务）

| 表名 | 所属库 | 读/写 | 谁写 | 关键列 | 业务含义 |
| --- | --- | --- | --- | --- | --- |
| `xxa_issues_records` | app MySQL | 写 | `app_records.save_issue_results` | `task_oid, business, subclass, last_time, key_issues(=symptom), create_time, response_time, error_msg` | 焦点问题记录：按工单维度看"这是什么业务、什么故障现象" |
| `xxa_complaint_word_cloud` | app MySQL | 写 | 同上 | `task_oid, key_word(=symptom), last_time, create_time, response_time, error_msg` | 词云数据：把故障现象当关键词统计 |
| `xxa_bomc_qa_records` | app MySQL | 写 | 同上 | 8 字段 + `task_oid, last_time, create_time, response_time, error_msg` | 有厂商处理意见的**全量**解析结果 |
| `xxa_bomc_qa_non_vendor` | app MySQL 写 / QA MySQL 读 | 写 + 读 | `app_records.save_issue_results` / `qa_knowledge.read_non_vendor` | 同上（无 `fldColumn4` 原始列） | 无厂商处理意见的工单，是 QA 聚类的唯一输入 |
| `xxa_city_qa_knowledge` | QA MySQL | 写 | `qa_knowledge.save_qa_knowledge` | `question, answer, knowledge_source, work_id, name, city, city_text, task_oids` | 面向向量检索/RAG 的标准问答对 |
| `xiaoxin`（opera 库） | GBase | 只读 | —（上游系统维护） | `TASK_OID, TASK_TITLE, fld_faultcontent, fld_column_2` | 工单原始：标题、报障内容、分公司处理意见 |

**8 个提取字段**（模型输出 → 入库列）的唯一定义是 `xiaoxin_work_order/fields.py` 的 `FIELDS`：`business` 业务、`device` 设备、`subclass` 二级分类、`symptom` 故障现象、`symptom_detail` 故障详情、`cause` 故障原因、`branch_actions` 分公司操作（JSON 数组字符串）、`vendor_actions` 厂商操作（JSON 数组字符串）。代码、测试与文档都从该常量派生，不再各抄一遍。另有 3 个元数据列：`create_time` 入库时间、`response_time` 模型响应耗时（毫秒）、`error_msg` 失败原因。

**分流规则**：`fldColumn4`（厂商处理意见）有实质内容 → 前 3 张表；无实质内容 → `xxa_bomc_qa_non_vendor`，且该批数据的 `vendor_actions` 会被置空。判据统一走 `common.is_valid_content()`（与 `to_text()` 共用 `BLANK_MARKERS`：空值 / `nan` /「无」/「无无」）。分流只在本模块做一次，业务层不用关心。

---

## 4. 对外 API 速查

统一从 `xiaoxin_work_order.db` 导入即可，不要深入子模块：

```python
from xiaoxin_work_order.db import (
    save_issue_results,      # app 库：一次批次写 4 张表
    read_non_vendor,         # QA 库：按时间读输入
    get_records_by_ids,      # QA 库：按 id 反查
    save_qa_knowledge,       # QA 库：写标准问答对
    query_gbase_by_task_oids,  # GBase：按 task_oid 查原始明细
    APP_TABLES,              # 4 张 app 表白名单（verify_env 自检用）
)
```

| 函数 | 签名 | 作用 | 异常/失败行为 |
| --- | --- | --- | --- |
| `save_issue_results` | `(final_df: pd.DataFrame) -> dict[str, int]` | 分流写 4 张表，返回 `{表名: 写入行数}`（`0`=该分流无数据，`-1`=写入异常） | 单表失败记 ERROR 日志并返回 `-1`，不影响其他表；pipeline 据此记 WARNING |
| `clean_old_records` | `(table_name, task_oid_list, engine=None)` | 按 task_oid 删旧（写前自动调用） | 表名不在白名单直接抛 `ValueError`；删失败记日志 |
| `read_non_vendor` | `(start_time, end_time) -> pd.DataFrame` | 按 `last_time` 读时间段数据 | 异常向上抛给 pipeline 统一兜底 |
| `get_records_by_ids` | `(id_list) -> pd.DataFrame` | 按 id 批量反查 | 失败返回空 DataFrame |
| `save_qa_knowledge` | `(qa_data: list[dict]) -> int` | 批量 INSERT 标准问答对，返回条数 | 失败 rollback、记日志、返回 0 |
| `query_gbase_by_task_oids` | `(task_oid_list: list) -> list[dict]` | 查工单原始明细 | 失败/无结果返回 `[]` |

---

## 5. 读写内容示例（重点）

以下示例全部使用虚构数据，方便对照字段理解业务。

### 5.1 写：一次每日入库

业务层把「接口原始工单 + 模型 8 字段 + 元数据」拼成 `final_df`，一行长这样：

| 列 | 示例值 | 说明 |
| --- | --- | --- |
| `task_oid` | `T202604010001` | 工单号，来自接口 `taskOid`，**幂等键** |
| `last_time` | `2026-04-01 09:12:33` | 接口 `lastTime`，QA 按它筛时间段 |
| `fldColumn4` | `已更换光猫端口，业务恢复` | 厂商处理意见，**仅用于分流**，入库前删除 |
| `business` | `宽带业务` | 模型提取 |
| `device` | `光猫` | 模型提取 |
| `subclass` | `宽带办理与维护` | 模型提取 |
| `symptom` | `宽带无法使用` | 模型提取，同时是焦点问题 / 词云关键词 |
| `symptom_detail` | `光猫亮红灯` | 模型提取 |
| `cause` | `光猫断电` | 模型提取 |
| `branch_actions` | `[{"operation":"重启光猫","result":"未恢复","suggestion":"派单"}]` | 模型提取，JSON 数组字符串 |
| `vendor_actions` | `[{"operation":"更换端口","result":"已恢复","suggestion":"观察"}]` | 模型提取 |
| `create_time` | `2026-04-02 01:00:05` | 入库时间 |
| `response_time` | `1830` | 模型耗时 ms |
| `error_msg` | `` | 空表示成功 |

调用：

```python
from xiaoxin_work_order.db import save_issue_results

counts = save_issue_results(final_df)
# {'xxa_issues_records': 1, 'xxa_complaint_word_cloud': 1,
#  'xxa_bomc_qa_records': 1, 'xxa_bomc_qa_non_vendor': 0}
```

这一行因为 `fldColumn4` 有效，会写进前 3 张表，各自写入的内容是：

```sql
-- 1) 先按幂等键删旧（值参数化，表名来自白名单）
DELETE FROM xxa_bomc_qa_records WHERE task_oid IN ('T202604010001');

-- 2) 追加写（示意，实际由 pandas.to_sql 完成）
INSERT INTO xxa_bomc_qa_records
  (task_oid, last_time, business, device, subclass, symptom, symptom_detail,
   cause, branch_actions, vendor_actions, create_time, response_time, error_msg)
VALUES
  ('T202604010001', '2026-04-01 09:12:33', '宽带业务', '光猫', '宽带办理与维护',
   '宽带无法使用', '光猫亮红灯', '光猫断电',
   '[{"operation":"重启光猫","result":"未恢复","suggestion":"派单"}]',
   '[{"operation":"更换端口","result":"已恢复","suggestion":"观察"}]',
   '2026-04-02 01:00:05', 1830, '');
```

另两张表只取自己需要的列：

| 表 | 写入的示例行 |
| --- | --- |
| `xxa_issues_records` | `task_oid=T202604010001, business=宽带业务, subclass=宽带办理与维护, last_time=2026-04-01 09:12:33, key_issues=宽带无法使用, create_time=…, response_time=1830, error_msg=''` |
| `xxa_complaint_word_cloud` | `task_oid=T202604010001, key_word=宽带无法使用, last_time=…, create_time=…, response_time=1830, error_msg=''` |

如果同一批里另有一条 `fldColumn4` 为空的工单 `T202604010002`（`vendor_actions` 已被置空），它只会进第 4 张表，写入列同上但不含 `fldColumn4`。

### 5.2 读：QA 生成的输入

```python
from xiaoxin_work_order.db import read_non_vendor, get_records_by_ids

df = read_non_vendor("2026-04-01", "2026-04-28")
# 等价 SQL：
#   SELECT * FROM xxa_bomc_qa_non_vendor
#   WHERE last_time >= '2026-04-01 00:00:00' AND last_time <= '2026-04-28 23:59:59'
```

返回的 `df` 每行是上面 `final_df` 去掉 `fldColumn4` 后写入的列，并额外带有 `id` 列（`get_records_by_ids` 按它反查），典型一行：

| `id` | `task_oid` | `last_time` | `symptom` | `symptom_detail` | `cause` | `branch_actions` | … |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `1024` | `T202604010002` | `2026-04-01 10:03:11` | `宽带无法使用` | `光猫亮红灯` | `光猫断电` | `[{"operation":"插电","result":"已恢复","suggestion":"无"}]` | … |

业务层随后剔除 `symptom_detail == "其他"` 与 `branch_actions` 等于具名常量 `EMPTY_BRANCH_ACTIONS`（模型「全无」输出）的记录，按 `symptom` 分组、每 400 条一批送模型聚类。**过滤与聚类不在本模块**，本模块只保证"原样把数据读出来"。

需要按聚类结果里的 `matched_issue_ids` 反查工单时：

```python
detail = get_records_by_ids([1024, 1025])
# SELECT * FROM xxa_bomc_qa_non_vendor WHERE id IN (1024, 1025)
# 常用于 --enrich-excel：拿反查到的 task_oid 再去 GBase 补明细
```

### 5.3 读：GBase 工单原始明细（只读）

```python
from xiaoxin_work_order.db import query_gbase_by_task_oids

rows = query_gbase_by_task_oids(["T202604010002"])
# 等价 SQL：
#   SELECT TASK_OID, TASK_TITLE, fld_faultcontent, fld_column_2
#   FROM xiaoxin WHERE TASK_OID IN ('T202604010002')
```

返回：

```python
[{
    "TASK_OID": "T202604010002",
    "TASK_TITLE": "宽带无法上网",
    "fld_faultcontent": "光猫亮红灯，家里宽带连不上",
    "fld_column_2": "已上门重启光猫并插电，业务恢复",
}]
```

字段对照：`TASK_TITLE` → 报障标题、`fld_faultcontent` → 报障内容、`fld_column_2` → 分公司处理意见。QA 流程用它生成 `data/gbase_<起>_<止>.xlsx` 明细；聚类输入由 `task_oid` / `symptom` / `symptom_detail` / `cause` / `formatted_actions` 拼成，不含 GBase 字段。

### 5.4 写：标准问答对

```python
from xiaoxin_work_order.db import save_qa_knowledge

qa_data = [{
    "standard_question": "宽带无法上网且光猫亮红灯如何处理",
    "standard_answer": "【问题原因】光猫断电。【解决方案】重新插电并重启光猫，观察指示灯是否恢复。",
    "matched_issue_ids": [1024, 1025],
}]
n = save_qa_knowledge(qa_data)   # 返回 1
```

实际写入 `xxa_city_qa_knowledge` 的一行（列顺序固定）：

| `question` | `answer` | `knowledge_source` | `work_id` | `name` | `city` | `city_text` | `task_oids` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 宽带无法上网且光猫亮红灯如何处理 | 【问题原因】…【解决方案】… | 工单提取 | system | 知识管理平台 | 999 | 全省 | `1024,1025` |

其中 `knowledge_source / work_id / name / city / city_text` 是本模块填充的固定值；`task_oids` 由 `common.format_issue_ids()` 把 `matched_issue_ids` 统一成逗号分隔字符串得到（反向解析用 `common.parse_issue_ids()`），表示这条标准问答由哪些工单聚类而来。

---

## 6. 幂等、事务与错误处理

- **app 4 张表**：每次写入前按本批 `task_oid` 执行 `DELETE ... WHERE task_oid IN (...)`，再 `to_sql(..., if_exists="append")`，所以同一天重复跑不会产生重复行。DELETE 使用 SQLAlchemy `expanding` 绑定参数，表名取自 `APP_TABLES` 白名单，避免拼接注入。
- **每张表独立 try/except**：某张表写失败只记 ERROR 日志、在该表计数里返回 `-1`，并继续写其他表；`save_issue_results` 返回的 dict 可直接看哪张表写了几行，pipeline 接收后据此记 WARNING，不再无条件打印「完成」。
- **QA 表**：`executemany` 一次提交，失败 `rollback` 并返回 0；该表是**追加写**，不做按 id 去重。
- **连接**：引擎缓存复用；pymysql 原生连接 `try/finally` 关闭；`pool_pre_ping=True` + `pool_recycle=3600` 防长连接失效。

## 7. 边界与约定

- 本模块**不建表、不改表结构**（没有 DDL）。表由 DBA 预建，`python -m xiaoxin_work_order.cli.verify_env` 会只读检查 4 张 app 表是否存在。
- 4 张 app 表与 `xxa_city_qa_knowledge` 的列类型、主键与 `id` 列定义由数据库管理员提供（责任人占位 / 获取渠道占位）；表缺失时 `xiaoxin_work_order.cli.verify_env` 对 app 表输出 `WARN 缺少表: ...`。
- 本模块**不做业务过滤、不调模型、不发 HTTP**：过滤规则、聚类、Excel 导出都在 `pipelines/qa_pipeline.py`；取数接口在 `xiaoxin_work_order/api/backend.py`。
- 上游字段改名（如接口 `taskOid` → 别的名字）改 `pipelines/issue_pipeline.py`；**列名/表名/固定值变动改本模块**，并在本 README 同步示例。
- `xxa_bomc_qa_non_vendor` 由 app 库写入、由 QA 库读取，两张库同名表是同一份业务数据的两个连接地址，排查数据不一致时先核对 `MYSQL_CONFIG` 与 `QA_MYSQL_CONFIG`。

## 8. 自检

```bash
python -m xiaoxin_work_order.cli.verify_env                          # 5 项全查（含 4 张 app 表是否存在）
python -m xiaoxin_work_order.cli.verify_env --skip-model --skip-api  # 只查 3 个数据库
python tests/check_prompts.py                             # 提示词/映射基线（不涉及数据库）
```

`xiaoxin_work_order.cli.verify_env` 的数据库检查只做 `SELECT VERSION()` / `information_schema` 查询，不写入任何业务表；未加 `--skip-api` / `--skip-model` 时另会向后端取数接口与模型服务各发起一次连通性调用；`APP_TABLES` 常量即来自本模块。
