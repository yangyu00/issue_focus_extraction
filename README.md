# issue_focus_extraction（模块化重构版）

工单解析入库 + QA 知识生成。

本次改动**只调整代码组织**：原来除 `prompts/` 外所有 `.py` 都堆在根目录，现在按职责拆进 `xiaoxin/` 包，并把散落在 `app.py` / `generate_qa.py` 里的全部数据库读写收口到 `xiaoxin/db/`，配了模块级说明。**CLI 参数、SQL、表名、字段、过滤规则、提示词、Docker 命令全部保持不变**，可以直接替换上线。

> 想理解业务数据怎么存、怎么读，先看 [`xiaoxin/db/README.md`](xiaoxin/db/README.md)：里面有表字典、字段对照和完整的读写内容示例。

---

## 一、快速开始（命令与重构前完全一致）

| 功能 | 命令 | 说明 |
| --- | --- | --- |
| 1. 每日定时处理 | `python app.py` | 默认：启动时先跑昨天，然后每天 `01:00` 跑昨天，常驻调度 |
| 2. 手动批量处理 | `python app.py --mode batch --start 2026-04-01 --end 2026-04-28` | 按天补跑 |
| 3. 手动生成 QA | `python generate_qa.py --start 2026-04-01 --end 2026-04-28` | 基于已入库结果聚类生成标准问答对 |

```bash
python app.py --mode daily                       # 只跑昨天一次
python app.py --mode date --date 2026-04-01       # 跑指定单日
python app.py --mode batch --start ... --end ... --at 02:00 --no-run-now
python generate_qa.py --start ... --end ... --qa-excel          # 额外导出 QA Excel
python generate_qa.py --enrich-excel ./data/包月费问题.xlsx     # 反查 GBase 明细
python verify_env.py                                          # 上线前只读自检
python tests/smoke_test.py                                     # 离线回归
python tests/check_prompts.py                                  # 提示词/映射基线
```

根目录保留的 `app.py` / `generate_qa.py` / `verify_env.py` 只是约 10 行的**兼容入口**（`import` 转发），不含业务逻辑；Dockerfile 的 `CMD ["python", "app.py"]` 因此不用改。

---

## 二、目录结构

```
issue_focus_extraction/
├── app.py                     # 兼容入口（→ xiaoxin/cli/app.py）
├── generate_qa.py             # 兼容入口（→ xiaoxin/cli/generate_qa.py）
├── verify_env.py              # 兼容入口（→ xiaoxin/cli/verify_env.py）
├── README.md                  # 本文件：项目级说明
├── .env / .env.example        # 本地配置 / 脱敏配置模板
├── .gitignore / .dockerignore
├── Dockerfile / docker-compose.yml
├── requirements.txt
├── prompts/                   # 9 段提示词，纯文本维护（唯一不进 Python 包的资产）
│   ├── device.txt  business.txt  subclass.txt  symptom.txt
│   ├── symptom_detail.txt  cause.txt  branch_actions.txt
│   ├── vendor_actions.txt  qa_cluster.txt
├── tests/
│   ├── source_snapshot.json   # 提示词/映射基线
│   ├── check_prompts.py       # 基线比对（零依赖）
│   └── smoke_test.py          # 伪造模型服务的离线回归
├── logs/  data/               # 运行产物（已 ignore）
└── xiaoxin/                   # 业务包
    ├── __init__.py
    ├── config.py              # 集中配置（PROJECT_PATH 指向仓库根）
    ├── logging_setup.py       # loguru 日志（原 utils.py）
    ├── common.py              # 跨层纯函数（to_text / is_valid_content / dedup_device_value）
    ├── api/
    │   └── backend.py         # 后端取数接口（HTTP，只读）
    ├── llm/
    │   ├── client.py          # OpenAI 兼容直连客户端（限流/重试/流式/清洗）
    │   ├── prompt_loader.py   # 提示词加载与占位符注入
    │   └── mappings.py        # 分类映射字典 MAPPING
    ├── extraction/
    │   └── workflow.py        # 8 字段提取工作流编排
    ├── db/                    # ★ 数据库读写统一层
    │   ├── README.md          # ★ 模块级说明：作用 + 读写内容示例
    │   ├── connections.py     # 连接/引擎工厂（唯一建连接的地方）
    │   ├── app_records.py     # app 库 4 张表的分流写入
    │   ├── qa_knowledge.py    # QA 库：读非厂商工单 / 按 id 反查 / 写标准问答
    │   └── gbase.py           # GBase opera.xiaoxin 明细（只读）
    ├── pipelines/
    │   ├── issue_pipeline.py  # 取数 → 提取 → 组装 → 入库（原 app.py 业务部分）
    │   └── qa_pipeline.py     # 过滤 → 分组聚类 → 写库/导出（原 generate_qa.py 业务部分）
    └── cli/
        ├── app.py             # 参数解析 + 每日调度
        ├── generate_qa.py     # QA 生成参数解析
        └── verify_env.py      # 上线前只读连通性自检
```

---

## 三、分层职责

| 层 | 目录 | 做什么 | 不做什么 |
| --- | --- | --- | --- |
| 入口 | 根目录 3 个 shim / `xiaoxin/cli/` | 解析参数、调度循环 | 不写 SQL、不调模型 |
| 编排 | `xiaoxin/pipelines/` | 串起取数、提取、过滤、聚类、导出 | 不建连接、不拼 SQL |
| 提取 | `xiaoxin/extraction/` + `xiaoxin/llm/` | 8 字段工作流、模型调用、提示词、映射 | 不碰数据库 |
| 外部接口 | `xiaoxin/api/` | 后端取数 HTTP 调用 | 不落库 |
| **数据** | **`xiaoxin/db/`** | **全部 SQL、连接、表名、字段拼装** | **不做业务过滤、不调模型、不建表** |
| 基础 | `config.py` / `logging_setup.py` / `common.py` | 配置、日志、纯函数 | — |

依赖方向单一：`cli → pipelines → {api, extraction, llm, db} → config`，不会反向引用。

---

## 四、文件迁移对照表

| 重构前（根目录） | 重构后 |
| --- | --- |
| `app.py`（main + 调度 + 业务 + 4 表 SQL） | `xiaoxin/cli/app.py`（main/调度）+ `xiaoxin/pipelines/issue_pipeline.py`（业务）+ `xiaoxin/db/app_records.py`（写库）+ 根目录 shim |
| `generate_qa.py`（QA 全流程 + GBase/MySQL SQL） | `xiaoxin/cli/generate_qa.py` + `xiaoxin/pipelines/qa_pipeline.py` + `xiaoxin/db/qa_knowledge.py` / `gbase.py` + 根目录 shim |
| `config.py` | `xiaoxin/config.py` |
| `utils.py` | `xiaoxin/logging_setup.py`（另拆出 `xiaoxin/common.py`） |
| `llm_client.py` | `xiaoxin/llm/client.py` |
| `prompt_loader.py` | `xiaoxin/llm/prompt_loader.py` |
| `mappings.py` | `xiaoxin/llm/mappings.py` |
| `issue_workflow.py` | `xiaoxin/extraction/workflow.py` |
| `verify_env.py` | `xiaoxin/cli/verify_env.py` + 根目录 shim |
| —（新增） | `xiaoxin/api/backend.py`、`xiaoxin/db/*`、`xiaoxin/common.py`、`.env.example` |
| 未改动 | `prompts/*.txt`、`tests/source_snapshot.json`、`requirements.txt`、`.env`、`Dockerfile`、`docker-compose.yml` |

`config.py` 移入包内后，`PROJECT_PATH` 取包的上一级，所以 `.env` / `prompts/` / `logs/` / `data/` 的落盘位置与重构前**完全相同**。

---

## 五、数据库模块（`xiaoxin/db/`）

业务代码现在只需要这样用，不再出现 `create_engine` / `to_sql` / `DELETE` / `INSERT`：

```python
from xiaoxin.db import (
    save_issue_results,        # 一次批次写 app 库 4 张表（内部按 fldColumn4 分流）
    read_non_vendor,           # 按时间读非厂商工单
    get_records_by_ids,        # 按 id 反查
    save_qa_knowledge,         # 写标准问答对
    query_gbase_by_task_oids,  # 查 GBase 工单原文
)
```

| 文件 | 管什么 |
| --- | --- |
| `connections.py` | 三套数据源的连接/引擎工厂，按需创建并缓存 |
| `app_records.py` | `xxa_issues_records` / `xxa_complaint_word_cloud` / `xxa_bomc_qa_records` / `xxa_bomc_qa_non_vendor` |
| `qa_knowledge.py` | 读 `xxa_bomc_qa_non_vendor`、写 `xxa_city_qa_knowledge` |
| `gbase.py` | 只读 `opera.xiaoxin` |

**表字典、字段含义、幂等规则，以及"一次入库写什么 / QA 读出来长什么样 / 标准问答对怎么落库"的完整示例，见 [`xiaoxin/db/README.md`](xiaoxin/db/README.md)。**

---

## 六、行为不变清单（可放心替换）

- CLI 参数与默认值、`schedule` 每日 `01:00` 调度、启动时先跑昨天；
- `Dockerfile` 的 `CMD`、`docker-compose.yml` 的 `command` / `env_file` / 卷挂载；
- 环境变量优先级：显式 `export` > `.env` > `config.py` 内置默认值；日志文件名与轮转；
- 后端接口字段（`taskOid / lastTime / taskTitle / fldFaultcontent / fldColumn2 / fldColumn4`）、`code==200 && success` 校验；
- 8 字段输出顺序与清洗规则、映射链、`/no_think` 后缀、25 条 `MAPPING` 与 9 段提示词（基线逐字未动）；
- 4 张表的表名/列名、`key_issues` 与 `key_word` 均取 `symptom`、按 `fldColumn4` 分流、写前按 `task_oid` 删旧；
- QA 过滤规则（`symptom_detail != "其他"`、空操作数组）、`CHUNK_SIZE=400`、按 `symptom` 分组、Excel 文件名格式；
- QA 表列顺序 `question, answer, knowledge_source, work_id, name, city, city_text, task_oids` 与固定值；
- 并发与节奏参数（`MAX_WORKERS` / `FIELD_WORKERS` / `LLM_QPS` / `*_SLEEP`）。

两处**实现层面**的改进（不改变业务结果）：连接改为按需懒加载并缓存，import 时不再建引擎；`DELETE` 改为 SQLAlchemy `expanding` 参数绑定 + 表名白名单，消除字符串拼接注入面。

---

## 七、配置与环境变量

`xiaoxin/config.py` 启动时加载仓库根的 `.env`，也支持同名环境变量覆盖；`.env.example` 是脱敏模板。真实地址与凭据不写入本文档。

| 变量组 | 用途 |
| --- | --- |
| `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` | app 入库 MySQL（`MYSQL_CONFIG`） |
| `QA_DB_HOST` / `QA_DB_PORT` / `QA_DB_USER` / `QA_DB_PASSWORD` / `QA_DB_NAME` | QA 读写 MySQL（`QA_MYSQL_CONFIG`） |
| `GBASE_HOST` / `GBASE_PORT` / `GBASE_USER` / `GBASE_PASSWORD` / `GBASE_DB` | GBase opera 明细查询 |
| `BACKEND_API_URL` | 后端取数接口 |
| `LLM_URL` / `LLM_API_KEY` / `LLM_MODEL` / `QA_LLM_MODEL` | 直连模型服务；提取与 QA 聚类分别用两个模型名 |
| `LLM_QPS` / `LLM_MAX_RETRIES` / `LLM_TIMEOUT` / `LLM_STREAM` / `LLM_TEMPERATURE` | 限流、重试、超时、流式、采样温度 |
| `MAX_WORKERS` / `FIELD_WORKERS` / `POST_CALL_SLEEP` / `BATCH_DAY_SLEEP` / `SCHEDULE_TIME` | 并发与节奏 |

运行前需可达：后端取数接口、app MySQL、QA MySQL、GBase、模型服务。常驻进程只有 `app.py`；`generate_qa.py` / `verify_env.py` / `tests/*` 为一次性任务。

---

## 八、工作流与数据契约（摘要）

**8 字段提取**：6 个独立字段并发（`device / business / cause / branch_actions / vendor_actions / symptom_detail`），业务 → `MAPPING` → 二级分类 → `MAPPING` → 故障现象串行；结果经 `clean_field()` 去 `<think>`/空格后按固定顺序组装 JSON。输出字段：

`device` 设备、`business` 业务、`subclass` 二级分类、`symptom` 故障现象、`symptom_detail` 故障详情、`cause` 故障原因、`branch_actions` 分公司操作、`vendor_actions` 厂商操作；另加 `create_time` 入库时间、`response_time` 模型耗时(ms)、`error_msg` 失败原因。

**入库分流**：`fldColumn4`（厂商处理意见）有效 → `xxa_issues_records` + `xxa_complaint_word_cloud` + `xxa_bomc_qa_records`；无效 → `xxa_bomc_qa_non_vendor`（`vendor_actions` 置空）。写前按 `task_oid` 删旧。

**QA 生成**：读 `xxa_bomc_qa_non_vendor` → 过滤 → 按 `symptom` 分组、每 400 条一批 → 查 GBase 补原文 → 模型聚类 → 统一写 `xxa_city_qa_knowledge`。详细字段示例见 `xiaoxin/db/README.md`。

---

## 九、部署与自检

```bash
# 构建运行（命令与重构前一致）
docker build -t xiaoxin:v1 .
docker run -dit --name xiaoxin_qa --env-file .env \
  -v $(pwd)/logs:/app/logs -v $(pwd)/data:/app/data xiaoxin:v1
# 或
docker compose up -d

# 临时容器：批量补跑 / QA 生成 / 自检
docker compose run --rm xiaoxin python app.py --mode batch --start 2026-04-01 --end 2026-04-28
docker compose run --rm xiaoxin python generate_qa.py --start 2026-04-01 --end 2026-04-28
docker compose run --rm xiaoxin python verify_env.py

# 本机
python verify_env.py                          # 5 项全查
python verify_env.py --skip-model --skip-api  # 只查 3 个数据库
python tests/smoke_test.py                    # 非流式 / --stream 流式
python tests/check_prompts.py                 # 输出「全部一致：9 段提示词 + 25 条映射」
```

`verify_env.py` 全部只读，不写业务表；`smoke_test.py` 起本地伪造模型服务，不碰真实数据库与接口。

---

## 十、安全与维护提示

- 原仓库约定 `.env` 随项目交付且填有真实凭据，本次为保持"开箱即跑"原样保留；**生产环境建议改用环境变量/密钥管理注入，并把 `.env` 从版本库移除**；新增的 `.env.example` 可作为脱敏模板。
- `.idea/` 是编辑器元数据，已在 `.gitignore` / `.dockerignore` 中忽略，可随时删除，不影响运行。
- 改提示词 → 改 `prompts/*.txt` 并同步 `tests/source_snapshot.json`；改表结构/固定值 → 改 `xiaoxin/db/` 对应文件并同步 `xiaoxin/db/README.md` 的示例。
