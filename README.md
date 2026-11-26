# issue_focus_extraction（模块化重构版）

工单解析入库 + QA 知识生成。

> 想理解业务数据怎么存、怎么读，先看 [`xiaoxin/db/README.md`](xiaoxin/db/README.md)：里面有表字典、字段对照和完整的读写内容示例。

---

## 一、快速开始

| 功能 | 命令 | 说明 |
| --- | --- | --- |
| 1. 每日定时处理 | `python -m xiaoxin.cli.app` | 默认：启动时先跑昨天，然后每天 `01:00` 跑昨天，常驻调度 |
| 2. 手动批量处理 | `python -m xiaoxin.cli.app --mode batch --start 2026-04-01 --end 2026-04-28` | 按天补跑 |
| 3. 手动生成 QA | `python -m xiaoxin.cli.generate_qa --start 2026-04-01 --end 2026-04-28` | 基于已入库结果聚类生成标准问答对 |

```bash
python -m xiaoxin.cli.app --mode daily                       # 只跑昨天一次
python -m xiaoxin.cli.app --mode date --date 2026-04-01       # 跑指定单日
python -m xiaoxin.cli.app --mode batch --start ... --end ... --at 02:00 --no-run-now
python -m xiaoxin.cli.generate_qa --start ... --end ... --qa-excel          # 额外导出 QA Excel
python -m xiaoxin.cli.generate_qa --enrich-excel ./data/包月费问题.xlsx     # 反查 GBase 明细
python -m xiaoxin.cli.verify_env                                          # 上线前只读自检
python tests/smoke_test.py                                     # 离线回归
python tests/check_prompts.py                                  # 提示词/映射基线
```

命令行入口统一收敛在 `xiaoxin/cli/`：根目录不再保留 `app.py` / `generate_qa.py` / `verify_env.py` 这类只为兼容旧调用路径而存在的转发壳，参数解析与调度逻辑只保留一份；Dockerfile 的 `CMD` 也直接指向 `xiaoxin.cli.app`。容器启动统一走根目录的 [`run.sh`](run.sh)（纯 `docker run`，不使用 docker compose），用法见 [`run.sh使用说明.txt`](run.sh使用说明.txt)。

---

## 二、目录结构

```
issue_focus_extraction/
├── run.sh                     # docker run 启动脚本（常驻服务 / 一次性任务）
├── run.sh使用说明.txt          # run.sh 的使用方法
├── README.md                  # 本文件：项目级说明
├── .env / .env.example        # 本地配置 / 脱敏配置模板
├── .gitignore / .dockerignore
├── Dockerfile                 # CMD → xiaoxin.cli.app
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
    │   ├── issue_pipeline.py  # 取数 → 提取 → 组装 → 入库
    │   └── qa_pipeline.py     # 过滤 → 分组聚类 → 写库/导出
    └── cli/
        ├── app.py             # 参数解析 + 每日调度（python -m xiaoxin.cli.app）
        ├── generate_qa.py     # QA 生成参数解析（python -m xiaoxin.cli.generate_qa）
        └── verify_env.py      # 上线前只读连通性自检（python -m xiaoxin.cli.verify_env）
```

---

## 三、分层职责

| 层 | 目录 | 做什么 | 不做什么 |
| --- | --- | --- | --- |
| 入口 | `xiaoxin/cli/` | 解析参数、调度循环 | 不写 SQL、不调模型 |
| 编排 | `xiaoxin/pipelines/` | 串起取数、提取、过滤、聚类、导出 | 不建连接、不拼 SQL |
| 提取 | `xiaoxin/extraction/` + `xiaoxin/llm/` | 8 字段工作流、模型调用、提示词、映射 | 不碰数据库 |
| 外部接口 | `xiaoxin/api/` | 后端取数 HTTP 调用 | 不落库 |
| **数据** | **`xiaoxin/db/`** | **全部 SQL、连接、表名、字段拼装** | **不做业务过滤、不调模型、不建表** |
| 基础 | `config.py` / `logging_setup.py` / `common.py` | 配置、日志、纯函数 | — |

依赖方向单一：`cli → pipelines → {api, extraction, llm, db} → config`，不会反向引用。

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

## 六、运行与数据契约

- CLI 参数与默认值、`schedule` 每日 `01:00` 调度、启动时先跑昨天；
- `Dockerfile` 的 `CMD`、`run.sh` 的 `docker run` 参数（`--env-file` / 卷挂载 / 重启策略）；
- 环境变量优先级：显式 `export` > `.env` > `config.py` 内置默认值；日志文件名与轮转；
- 后端接口字段（`taskOid / lastTime / taskTitle / fldFaultcontent / fldColumn2 / fldColumn4`）、`code==200 && success` 校验；
- 8 字段输出顺序与清洗规则、映射链、`/no_think` 后缀、25 条 `MAPPING` 与 9 段提示词（基线逐字未动）；
- 4 张表的表名/列名、`key_issues` 与 `key_word` 均取 `symptom`、按 `fldColumn4` 分流、写前按 `task_oid` 删旧；
- QA 过滤规则（保留 `symptom_detail != "其他"`、剔除 `branch_actions` 等于 `[{"operation":"无","result":"无","suggestion":"无"}]` 的记录）、`CHUNK_SIZE=400`、按 `symptom` 分组、Excel 文件名格式；
- QA 表列顺序 `question, answer, knowledge_source, work_id, name, city, city_text, task_oids` 与固定值；
- 并发与节奏参数（`MAX_WORKERS` / `FIELD_WORKERS` / `LLM_QPS` / `*_SLEEP`）。

连接按需懒加载并缓存，import 时不建引擎；`DELETE` 使用 SQLAlchemy `expanding` 参数绑定 + 表名白名单。

---

## 七、配置与环境变量

`xiaoxin/config.py` 启动时加载仓库根的 `.env`，也支持同名环境变量覆盖；`.env.example` 是脱敏模板。真实地址与凭据不写入本文档。容器基线为 `python:3.11-slim`、容器内时区 `Asia/Shanghai`（见 `Dockerfile`）；依赖及版本见 `requirements.txt`。

| 变量组 | 用途 |
| --- | --- |
| `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` | app 入库 MySQL（`MYSQL_CONFIG`） |
| `QA_DB_HOST` / `QA_DB_PORT` / `QA_DB_USER` / `QA_DB_PASSWORD` / `QA_DB_NAME` | QA 读写 MySQL（`QA_MYSQL_CONFIG`） |
| `GBASE_HOST` / `GBASE_PORT` / `GBASE_USER` / `GBASE_PASSWORD` / `GBASE_DB` | GBase opera 明细查询 |
| `BACKEND_API_URL` | 后端取数接口 |
| `LLM_URL` / `LLM_API_KEY` / `LLM_MODEL` / `QA_LLM_MODEL` | 直连模型服务；提取与 QA 聚类分别用两个模型名 |
| `LLM_QPS` / `LLM_MAX_RETRIES` / `LLM_TIMEOUT` / `LLM_STREAM` / `LLM_TEMPERATURE` | 限流、重试、超时、流式、采样温度 |
| `MAX_WORKERS` / `FIELD_WORKERS` / `POST_CALL_SLEEP` / `BATCH_DAY_SLEEP` / `SCHEDULE_TIME` | 并发与节奏 |

取值来源与缺失行为：`DB_*` / `QA_DB_*` / `GBASE_*` 的地址、账号、口令由数据库管理员提供（责任人占位）；`BACKEND_API_URL` 由后端取数接口提供方提供（责任人占位）；`LLM_URL` / `LLM_API_KEY` / `LLM_MODEL` / `QA_LLM_MODEL` 由模型服务提供方提供（责任人占位）。未在 `.env` 或环境变量中给出的键，回落到 `xiaoxin/config.py` 的同名内置默认值；`LLM_STREAM` 取值 `1` / `true` / `yes` / `on` 时启用流式，其余值按非流式处理。

运行前需可达：后端取数接口、app MySQL、QA MySQL、GBase、模型服务。常驻进程只有 `xiaoxin.cli.app`；`xiaoxin.cli.generate_qa` / `xiaoxin.cli.verify_env` / `tests/*` 为一次性任务。

---

## 八、工作流与数据契约（摘要）

**8 字段提取**：6 个独立字段并发（`device / business / cause / branch_actions / vendor_actions / symptom_detail`），业务 → `MAPPING` → 二级分类 → `MAPPING` → 故障现象串行；结果经 `clean_field()` 去 `<think>`/空格后按固定顺序组装 JSON。输出字段：

`device` 设备、`business` 业务、`subclass` 二级分类、`symptom` 故障现象、`symptom_detail` 故障详情、`cause` 故障原因、`branch_actions` 分公司操作、`vendor_actions` 厂商操作；另加 `create_time` 入库时间、`response_time` 模型耗时(ms)、`error_msg` 失败原因。

**入库分流**：`fldColumn4`（厂商处理意见）有效 → `xxa_issues_records` + `xxa_complaint_word_cloud` + `xxa_bomc_qa_records`；无效 → `xxa_bomc_qa_non_vendor`（`vendor_actions` 置空）。写前按 `task_oid` 删旧。

后端接口返回空结果时该日期批次直接跳过、不写库；响应缺少 `taskOid` / `fldColumn4` 时记 ERROR 日志并跳过，不写库（见 `xiaoxin/pipelines/issue_pipeline.py`）。

**QA 生成**：读 `xxa_bomc_qa_non_vendor` → 过滤 → 按 `symptom` 分组、每 400 条一批 → 查 GBase 补原文 → 模型聚类 → 统一写 `xxa_city_qa_knowledge`。详细字段示例见 `xiaoxin/db/README.md`。

---

## 九、部署与自检

镜像构建一次即可，之后只通过 `docker run` 启动，项目不再提供 docker compose 文件：

```bash
docker build -t xiaoxin:v1 .
cp .env.example .env          # 首次：按实际环境填写（已有 .env 可跳过）
./run.sh                      # 启动常驻每日调度服务
docker logs -f xiaoxin_qa     # 查看日志
```

[`run.sh`](run.sh) 把 `docker run` 的 `--env-file`、卷挂载、容器名与重启策略固化下来；不带参数启动常驻服务，带参数时用一次性容器执行指定命令（用完即删）。完整用法见 [`run.sh使用说明.txt`](run.sh使用说明.txt)，常见的一次性任务：

```bash
./run.sh python -m xiaoxin.cli.app --mode batch --start 2026-04-01 --end 2026-04-28
./run.sh python -m xiaoxin.cli.generate_qa --start 2026-04-01 --end 2026-04-28
./run.sh python -m xiaoxin.cli.verify_env
```

本机（不用容器）：

```bash
python -m xiaoxin.cli.verify_env                          # 5 项全查
python -m xiaoxin.cli.verify_env --skip-model --skip-api  # 只查 3 个数据库
python -m xiaoxin.cli.app --mode daily                    # 只跑昨天一次
python tests/smoke_test.py                                # 非流式 / --stream 流式
python tests/check_prompts.py                             # 输出「全部一致：9 段提示词 + 25 条映射」
```

`xiaoxin.cli.verify_env` 每项输出 `[PASS]` / `[FAIL]`，末行输出「汇总：N 项，通过 M，失败 K」，全部通过时退出码为 0、否则为 1；`tests/smoke_test.py` 末行输出 `ALL_TESTS_PASSED (stream=true|false)`。

`xiaoxin.cli.verify_env` 全部只读，不写业务表；`smoke_test.py` 起本地伪造模型服务，不碰真实数据库与接口。

---

## 十、安全与维护提示

- `.env` 随项目交付且填有真实凭据；**生产环境建议改用环境变量/密钥管理注入，并把 `.env` 从版本库移除**；`.env.example` 可作为脱敏模板。
- `.idea/` 是编辑器元数据，已在 `.gitignore` / `.dockerignore` 中忽略，可随时删除，不影响运行。
- 改提示词 → 改 `prompts/*.txt` 并同步 `tests/source_snapshot.json`；改表结构/固定值 → 改 `xiaoxin/db/` 对应文件并同步 `xiaoxin/db/README.md` 的示例。
