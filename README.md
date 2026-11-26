# issue_focus_extraction

工单解析入库 + QA 知识生成。

> 想理解业务数据怎么存、怎么读，先看 [`xiaoxin_work_order/db/README.md`](xiaoxin_work_order/db/README.md)：里面有表字典、字段对照和完整的读写内容示例。

---

## 一、快速开始

| 功能 | 命令 | 说明 |
| --- | --- | --- |
| 1. 每日定时处理 | `python -m xiaoxin_work_order.cli.app` | 默认：启动登记「昨天」，每天 `01:00` 登记；取数为空/未稳定则当天内重试，确认数据不再增长才入库 |
| 2. 手动批量处理 | `python -m xiaoxin_work_order.cli.app --mode batch --start 2026-04-01 --end 2026-04-28` | 按天补跑（不走就绪探测，直接取数入库） |
| 3. 手动生成 QA | `python -m xiaoxin_work_order.cli.generate_qa --start 2026-04-01 --end 2026-04-28` | 基于已入库结果聚类生成标准问答对 |

```bash
python -m xiaoxin_work_order.cli.app --mode daily                       # 只跑昨天一次（不探测）
python -m xiaoxin_work_order.cli.app --mode date --date 2026-04-01       # 跑指定单日（不探测）
python -m xiaoxin_work_order.cli.app --mode schedule --at 02:00 --no-run-now
python -m xiaoxin_work_order.cli.app --retry-interval-hours 1 --stability-recheck-minutes 10 --retry-window-end 23:00
python -m xiaoxin_work_order.cli.generate_qa --start ... --end ... --qa-excel          # 额外导出 QA Excel
python -m xiaoxin_work_order.cli.generate_qa --enrich-excel ./data/包月费问题.xlsx     # 反查 GBase 明细
python -m xiaoxin_work_order.cli.verify_env                                          # 上线前只读自检
python tests/smoke_test.py                                     # 离线回归
python tests/test_daily_retry.py                               # 就绪探测 / 当日重试（离线）
python tests/check_prompts.py                                  # 提示词/映射基线
```

命令行入口统一收敛在 `xiaoxin_work_order/cli/`，日期校验等公共逻辑抽到 `cli/validators.py`，日志只在各入口 `main()` 初始化一次；Dockerfile 的 `CMD` 也直接指向 `xiaoxin_work_order.cli.app`。容器启动统一走根目录的 [`run.sh`](run.sh)（纯 `docker run`，不使用 docker compose）：**整个项目挂载到 `/app`，宿主机改 `.env` / 代码后 `docker restart` 即生效**（改 `requirements.txt` 才需重新 build）；**容器以宿主机当前 uid/gid 非 root 运行**，写出的日志/Excel 属主就是你本人。用法见 [`how_to_run.txt`](how_to_run.txt)。

---

## 二、目录结构

```
issue_focus_extraction/
├── run.sh                     # docker run 启动脚本（整目录挂 /app，常驻服务 / 一次性任务）
├── how_to_run.txt             # run.sh 的使用方法（含 restart 生效流程）
├── README.md                  # 本文件：项目级说明
├── .env / .env.example        # 本地配置 / 脱敏配置模板
├── .gitignore / .dockerignore
├── Dockerfile                 # CMD → xiaoxin_work_order.cli.app
├── requirements.txt
├── prompts/                   # 9 段提示词，纯文本维护（唯一不进 Python 包的资产）
│   ├── device.txt  business.txt  subclass.txt  symptom.txt
│   ├── symptom_detail.txt  cause.txt  branch_actions.txt
│   ├── vendor_actions.txt  qa_cluster.txt
├── tests/
│   ├── _bootstrap.py          # 三个测试脚本共用的 sys.path 引导（零依赖）
│   ├── source_snapshot.json   # 提示词/映射基线
│   ├── check_prompts.py       # 基线比对 + FIELDS 清单校验（零依赖）
│   ├── smoke_test.py          # 伪造模型服务的离线回归
│   └── test_daily_retry.py    # 就绪探测 / 当日重试状态机（全注入，离线）
├── logs/  data/               # 运行产物（已 ignore）
└── xiaoxin_work_order/                   # 业务包
    ├── __init__.py
    ├── fields.py              # ★ 8 字段 / 提示词名 / 并发划分的唯一常量定义
    ├── config.py              # 集中配置（PROJECT_PATH 指向仓库根）
    ├── logging_setup.py       # loguru 日志（只在 CLI 入口 setup_logging 一次）
    ├── common.py              # 跨层纯函数（空值判据 / strip_think / issue ids / to_text）
    ├── scheduling.py          # 就绪探测指纹 + 当日重试状态机（EventKind / 纯逻辑）
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
    │   ├── app_records.py     # app 库 4 张表的分流写入（声明式列映射）
    │   ├── qa_knowledge.py    # QA 库：读非厂商工单 / 按 id 反查 / 写标准问答
    │   └── gbase.py           # GBase opera.xiaoxin 明细（只读）
    ├── pipelines/
    │   ├── issue_pipeline.py  # 取数（可复用探测快照）→ 提取 → 组装 → 入库
    │   └── qa_pipeline.py     # 过滤 → 分组聚类 → 写库/导出
    └── cli/
        ├── app.py             # 参数解析 + 每日调度主循环（python -m xiaoxin_work_order.cli.app）
        ├── generate_qa.py     # QA 生成参数解析（python -m xiaoxin_work_order.cli.generate_qa）
        ├── verify_env.py      # 上线前只读连通性自检（python -m xiaoxin_work_order.cli.verify_env）
        ├── validators.py      # 日期校验（两个入口共用）
        └── qa_excel.py        # --enrich-excel 手动反查命令
```

---

## 三、分层职责

| 层 | 目录 | 做什么 | 不做什么 |
| --- | --- | --- | --- |
| 入口 | `xiaoxin_work_order/cli/` | 解析参数、调度主循环（轮询驱动状态机） | 不写 SQL、不调模型 |
| 调度 | `xiaoxin_work_order/scheduling.py` | 就绪指纹、稳定判定、当日重试窗口 | 不取数、不入库（由外部注入回调） |
| 编排 | `xiaoxin_work_order/pipelines/` | 串起取数、提取、过滤、聚类、导出 | 不建连接、不拼 SQL |
| 提取 | `xiaoxin_work_order/extraction/` + `xiaoxin_work_order/llm/` | 8 字段工作流、模型调用、提示词、映射 | 不碰数据库 |
| 外部接口 | `xiaoxin_work_order/api/` | 后端取数 HTTP 调用 | 不落库 |
| **数据** | **`xiaoxin_work_order/db/`** | **全部 SQL、连接、表名、字段拼装** | **不做业务过滤、不调模型、不建表** |
| 基础 | `config.py` / `logging_setup.py` / `common.py` | 配置、日志、纯函数 | — |

依赖方向单一：`cli → {pipelines, scheduling} → {api, extraction, llm, db, fields} → {config, common}`；`scheduling.py` 只接收注入的 `fetch` / `on_stable` 回调，`on_stable` 在判定稳定的那次轮询里同步执行（见下），状态机本身不反向依赖任何业务模块，因此可离线单测。

---

## 四、数据库模块（`xiaoxin_work_order/db/`）

业务代码统一从 `xiaoxin_work_order.db` 导入：

```python
from xiaoxin_work_order.db import (
    save_issue_results,        # 一次批次写 app 库 4 张表（内部按 fldColumn4 分流）
    read_non_vendor,           # 按时间读非厂商工单
    get_records_by_ids,        # 按 id 反查
    save_qa_knowledge,         # 写标准问答对
    query_gbase_by_task_oids,  # 查 GBase 工单原文
)
```

| 文件 | 管什么 |
| --- | --- |
| `connections.py` | 三套数据源的连接/引擎工厂；`app_engine()` / `qa_engine()` 按需创建并缓存，`gbase_connection()` / `qa_connection()` 每次调用新建、用完即关 |
| `app_records.py` | `xxa_issues_records` / `xxa_complaint_word_cloud` / `xxa_bomc_qa_records` / `xxa_bomc_qa_non_vendor` |
| `qa_knowledge.py` | 读 `xxa_bomc_qa_non_vendor`、写 `xxa_city_qa_knowledge` |
| `gbase.py` | 只读 `opera.xiaoxin` |

**表字典、字段含义、幂等规则，以及"一次入库写什么 / QA 读出来长什么样 / 标准问答对怎么落库"的完整示例，见 [`xiaoxin_work_order/db/README.md`](xiaoxin_work_order/db/README.md)。**

---

## 五、运行与数据契约

- CLI 参数与默认值、`schedule` 每日 `01:00` **登记**批次（启动时也可登记一次）、就绪探测与当日重试状态机（见下）；
- `Dockerfile` 的 `CMD` 与 `USER 1000:1000`、`run.sh` 的 `docker run` 参数（**整目录挂载 `-v <项目根>:/app`** / `--user <宿主机uid>:<gid>` / `--restart unless-stopped`；刻意不用 `--env-file`，见「八、部署与自检」）；启动前自检 `logs` / `data` 可写且无旧 root 属主文件；
- 环境变量优先级：显式 `export` > `.env` > `config.py` 内置默认值；`config.py` 每次进程启动读 `/app/.env`，因此改配置后 `docker restart` 即生效；日志文件名与轮转；
- 日志契约：`logs/INFO` / `logs/ERROR` 下按天 `00:00` 轮转、保留 180 天、`compression=None`；文件名前缀 `xiaoxin_work_order-info` / `xiaoxin_work_order-error` / `generate_qa_knowledge-info` / `generate_qa_knowledge-error`（见 `xiaoxin_work_order/config.py` / `xiaoxin_work_order/logging_setup.py`）。`setup_logging()` 只在各 CLI 入口的 `main()` 调用一次，同时挂一个 stderr sink；业务事件统一走 `logger`（文件 + 屏幕），不再 `print` 与 `logger` 双发，`print` 只留给交互式横幅与自检报告；日志目录也在此时创建，import 不产生副作用。
- 后端接口：`GET` `BACKEND_API_URL`，查询参数 `startTime` / `endTime`（`YYYY-MM-DD HH:MM:SS`），`timeout=60`；响应需 `code==200 && success`，`result` 为工单列表，字段按 `taskOid / lastTime / taskTitle / fldFaultcontent / fldColumn2 / fldColumn4` 读取；返回非 JSON、业务码不符、网络异常或 `result` 为空时统一返回空 DataFrame（见 `xiaoxin_work_order/api/backend.py`）；
- 8 字段输出顺序与清洗规则、映射链、`/no_think` 后缀、25 条 `MAPPING` 与 9 段提示词；
- 4 张表的表名/列名、`key_issues` 与 `key_word` 均取 `symptom`、按 `fldColumn4` 分流、写前按 `task_oid` 删旧；
- QA 过滤规则（保留 `symptom_detail != "其他"`、剔除 `branch_actions` 等于具名常量 `EMPTY_BRANCH_ACTIONS` 的记录，即模型「全无」输出）、`CHUNK_SIZE=400`、按 `symptom` 分组、Excel 文件名格式；
- QA 表列顺序 `question, answer, knowledge_source, work_id, name, city, city_text, task_oids` 与固定值；
- `--enrich-excel <XLSX>` 输入契约（辅助命令已拆到 `cli/qa_excel.py`，属外部依赖）：输入 Excel 必须含 `matched_issue_ids` 列；获取渠道占位：由 `--qa-excel` 导出的 `data/qa_<起>_<止>.xlsx` 提供，责任人占位：QA 结果提供方；文件不存在或缺该列时记 ERROR 日志并终止，输出 `<输入名>_gbase_result.xlsx`。
- 并发与节奏参数（`MAX_WORKERS` / `FIELD_WORKERS` / `LLM_QPS` / `*_SLEEP`）、就绪探测参数（`RETRY_*` / `STABILITY_RECHECK_MINUTES` / `SCHEDULER_POLL_SECONDS`）。
- 模型输入与聚类输入契约：`build_labeled_input()` 按「标题 / 描述 / 分公司处理意见 / 厂商处理意见」四个标签拼接，分别取 `taskTitle` / `fldFaultcontent` / `fldColumn2` / `fldColumn4`，空值经 `to_text()` 归一为「无」；`device` 另经 `common.dedup_device_value()` 去除整串翻倍，`branch_actions` / `vendor_actions` 经 `_actions_to_str()` 规整为 JSON 数组字符串；QA 聚类每批输入行格式为 `task_oid: symptom :: symptom_detail :: cause :: formatted_actions`，其中 `formatted_actions` 由 `convert_actions()` 把 JSON 数组摊平成「操作 :: 结果 :: 建议」（见 `issue_pipeline.py` / `qa_pipeline.py` / `common.py`）；

### 每日调度：就绪探测与当日重试

上游库可能「昨天的数据还没准备好」（接口返回空），也可能「正在补写」（按日期查只拿到不完整快照）。`schedule` 模式由 `xiaoxin_work_order/scheduling.py` 的状态机判定上游数据是否就绪：

| 探测结果 | 动作 | 下次尝试 |
| --- | --- | --- |
| 返回空 | 记 INFO，不入库 | + `RETRY_INTERVAL_HOURS`（默认 1h） |
| 返回缺 `taskOid` 列 | 记 WARN，视为接口异常，不入库 | + 1h |
| 首次拿到非空 | 记下指纹，先做一次快速复核 | + `STABILITY_RECHECK_MINUTES`（默认 10min） |
| 指纹与上次不一致 / 记录数增长 | 仍不稳定，不入库 | + 1h |
| **指纹与上次一致** | **数据不再增长，用这份 df 跑模型入库** | 批次结束 |

- **指纹** = 行数 + `taskOid` 集合 + 最大 `lastTime`；只比行数会漏掉「删一条补一条、总数不变」。
- 探测只调后端取数接口，不跑模型、不写库；稳定后把当次 df 直接传给 `run_process_for_date(date, df=...)`，不再二次取数，避免两份快照不一致。
- 所有等待都表达为 `next_at`，由主循环每 `SCHEDULER_POLL_SECONDS`（默认 60s）轮询推进，等待期间**不 sleep 阻塞调度器**；`01:00` 的触发点只做登记。**判定稳定后，`DailyRetryQueue.tick()` 会在本次轮询里同步调用 `on_stable`（跑模型 + 入库，可能耗时数分钟），期间主循环会阻塞**；这是显式的回调副作用（见 `scheduling.py` 模块 docstring），不会 sleep 等下一次探测，也不影响次日 01:00 的登记。
- **三条硬边界**：跨运行日、到达 `RETRY_WINDOW_END`（默认 `23:00`）、达到 `MAX_RETRY_ATTEMPTS`（默认 24），任一满足即丢弃并记 WARN，**绝不带到转天**。放弃的日期需手动 `--mode batch --start <日期> --end <日期>` 补跑。
- 同一日期重复登记幂等；当天已成功入库的日期不会重跑模型（顺带避免启动那次与 01:00 那次重复消耗）。
- `--mode daily` / `date` / `batch` 保持原有「立即取数入库」行为，不走状态机，便于手动补数据。

连接按需懒加载并缓存，import 时不建引擎、也不建日志目录；`DELETE` 使用 SQLAlchemy `expanding` 参数绑定 + 表名白名单。`save_issue_results()` 返回的写入行数里 `-1` 表示该表写入异常，pipeline 会据此记 WARNING。

---

## 六、配置与环境变量

`xiaoxin_work_order/config.py` 每次进程启动加载项目根的 `.env`，也支持同名环境变量覆盖；`.env.example` 是脱敏模板。真实地址与凭据不写入本文档。容器基线为 `python:3.11-slim`、容器内时区 `Asia/Shanghai`（见 `Dockerfile`）；依赖及版本见 `requirements.txt`。本机（不用容器）运行需 Python 3.11 及以上：`xiaoxin_work_order/scheduling.py` 使用了 `enum.StrEnum`。

用 `run.sh` 启动时整个项目挂到容器 `/app`，宿主机改 `.env` / `prompts/*.txt` / 代码后 `docker restart` 即可生效，无需重新 build；改 `requirements.txt` 例外（依赖装在镜像层）。**优先级不变**：显式 `export` / `docker -e` > `/app/.env` > `config.py` 内置默认值；用 `docker -e` 注入的值只在创建容器时固化，改值需重建容器。

| 变量组 | 用途 |
| --- | --- |
| `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` | app 入库 MySQL（`MYSQL_CONFIG`） |
| `QA_DB_HOST` / `QA_DB_PORT` / `QA_DB_USER` / `QA_DB_PASSWORD` / `QA_DB_NAME` | QA 读写 MySQL（`QA_MYSQL_CONFIG`） |
| `GBASE_HOST` / `GBASE_PORT` / `GBASE_USER` / `GBASE_PASSWORD` / `GBASE_DB` | GBase opera 明细查询 |
| `BACKEND_API_URL` | 后端取数接口 |
| `LLM_URL` / `LLM_API_KEY` / `LLM_MODEL` / `QA_LLM_MODEL` | 直连模型服务；提取与 QA 聚类分别用两个模型名 |
| `LLM_QPS` / `LLM_MAX_RETRIES` / `LLM_TIMEOUT` / `LLM_STREAM` / `LLM_TEMPERATURE` | 限流、重试、超时、流式、采样温度 |
| `MAX_WORKERS` / `FIELD_WORKERS` / `POST_CALL_SLEEP` / `BATCH_DAY_SLEEP` / `SCHEDULE_TIME` | 并发与节奏 |
| `RETRY_INTERVAL_HOURS` / `STABILITY_RECHECK_MINUTES` / `RETRY_WINDOW_END` / `MAX_RETRY_ATTEMPTS` / `SCHEDULER_POLL_SECONDS` | 每日批次的就绪探测与当日重试 |

取值来源与缺失行为：`DB_*` / `QA_DB_*` / `GBASE_*` 的地址、账号、口令由数据库管理员提供（责任人占位）；`BACKEND_API_URL` 由后端取数接口提供方提供（责任人占位）；`LLM_URL` / `LLM_API_KEY` / `LLM_MODEL` / `QA_LLM_MODEL` 由模型服务提供方提供（责任人占位）；获取渠道占位：<由对应责任人在部署交付时经 `.env` 或显式环境变量下发>。未在 `.env` 或环境变量中给出的键，回落到 `xiaoxin_work_order/config.py` 的同名内置默认值；`LLM_STREAM` 取值 `1` / `true` / `yes` / `on` 时启用流式，其余值按非流式处理。

运行参数内置默认值（`xiaoxin_work_order/config.py`）：`LLM_TEMPERATURE=0.1`、`LLM_TIMEOUT=120`、`LLM_MAX_RETRIES=3`、`LLM_QPS=2.0`、`LLM_STREAM=false`、`MAX_WORKERS=1`、`FIELD_WORKERS=6`、`POST_CALL_SLEEP=5`、`BATCH_DAY_SLEEP=2`、`SCHEDULE_TIME=01:00`、`RETRY_INTERVAL_HOURS=1`、`STABILITY_RECHECK_MINUTES=10`、`RETRY_WINDOW_END=23:00`、`MAX_RETRY_ATTEMPTS=24`、`SCHEDULER_POLL_SECONDS=60`；非凭据类键未在 `.env` 或环境变量中给出时回落到上述默认值。

运行前需可达：后端取数接口、app MySQL、QA MySQL、GBase、模型服务。常驻进程只有 `xiaoxin_work_order.cli.app`；`xiaoxin_work_order.cli.generate_qa` / `xiaoxin_work_order.cli.verify_env` / `tests/*` 为一次性任务。

---

## 七、工作流与数据契约（摘要）

**8 字段提取**（字段清单、提示词文件名与并发/串行划分的唯一来源是 `xiaoxin_work_order/fields.py` 的 `FIELDS`，下面不再重复维护）：6 个独立字段并发（`device / business / cause / branch_actions / vendor_actions / symptom_detail`），业务 → `MAPPING` → 二级分类 → `MAPPING` → 故障现象串行；结果经 `clean_field()` 去 `<think>`/空格后按固定顺序组装 JSON。输出字段：

`device` 设备、`business` 业务、`subclass` 二级分类、`symptom` 故障现象、`symptom_detail` 故障详情、`cause` 故障原因、`branch_actions` 分公司操作、`vendor_actions` 厂商操作；另加 `create_time` 入库时间、`response_time` 模型耗时(ms)、`error_msg` 失败原因（由 `xiaoxin_work_order/pipelines/issue_pipeline.py` 的 `safe_run_model()` 与 `_build_final_df()` 生成）。

**入库分流**：`fldColumn4`（厂商处理意见）有效 → `xxa_issues_records` + `xxa_complaint_word_cloud` + `xxa_bomc_qa_records`；无效 → `xxa_bomc_qa_non_vendor`（`vendor_actions` 置空）。写前按 `task_oid` 删旧。「有效」与 `to_text()` 共用 `common.BLANK_MARKERS`（空值 / `nan` /「无」/「无无」），模型输入归一与入库分流不再各判一套。

后端接口返回空结果时该日期批次直接跳过、不写库；响应缺少 `taskOid` / `fldColumn4` 时记 ERROR 日志并跳过，不写库（见 `xiaoxin_work_order/pipelines/issue_pipeline.py`）。

**QA 生成**：读 `xxa_bomc_qa_non_vendor` → 过滤 → 按 `symptom` 分组、每 400 条一批 → 查 GBase 明细另存 `data/gbase_<起>_<止>.xlsx`（不进入模型输入）→ 模型聚类 → 统一写 `xxa_city_qa_knowledge`。详细字段示例见 `xiaoxin_work_order/db/README.md`。

---

## 八、部署与自检

镜像构建一次即可，之后只通过 `docker run` 启动：

```bash
docker build -t xiaoxin_work_order:v1 .
cp .env.example .env          # 首次：按实际环境填写（已有 .env 可跳过）
./run.sh                      # 启动常驻每日调度服务
docker logs -f xiaoxin_work_order_qa     # 查看日志
```

[`run.sh`](run.sh) 把 `docker run` 的整目录挂载、容器名与重启策略固化下来；不带参数启动常驻服务，带参数时用一次性容器执行指定命令（用完即删）。完整用法见 [`how_to_run.txt`](how_to_run.txt)，常见的一次性任务：

[`run.sh`](run.sh) 固化取值：镜像 `xiaoxin_work_order:v1`（`IMAGE` 覆盖）、常驻容器名 `xiaoxin_work_order_qa`（`NAME` 覆盖）、`-v <项目根>:/app`、`--user "$(id -u):$(id -g)"`（`RUN_UID` / `RUN_GID` 可覆盖）、`-e HOME=/tmp`、`--restart unless-stopped`；不带参数常驻，带参数以 `--rm` 一次性执行（同样挂载整个项目、同样非 root）；缺 `.env`、镜像不存在、目录不可写或残留 root 属主文件时报错退出。

### 整目录挂载：改配置后 restart 即可

`-v <项目根>:/app` 让容器内 `/app` 实时跟随宿主机目录，镜像里 `COPY . .` 的内容在运行时被覆盖：

| 宿主机改动 | 生效方式 |
| --- | --- |
| `.env` | `docker restart <容器名>`（进程重启时 `config.py` 重新读 `/app/.env`） |
| `prompts/*.txt` | 立即生效，`load_prompt()` 每次调用都读文件，连 restart 都不用 |
| `xiaoxin_work_order/*.py` | `docker restart`（模块已加载进内存） |
| `requirements.txt` | **必须重新 `docker build`**，依赖装在镜像层 |

```bash
vim .env                              # 宿主机直接改
docker restart xiaoxin_work_order_qa   # 不用 build、不用 rm
docker logs --tail 30 xiaoxin_work_order_qa
```

**为什么去掉 `--env-file .env`**：它是 `docker run` 客户端在**创建容器**时把文件内容固化进容器环境变量，`docker restart` 不会重新解析；而显式环境变量优先级又高于 `config.py` 读 `.env`，结果就是「改了 `.env`、restart 后仍是旧值」。去掉后由 `config.py` 每次启动读挂载进来的 `/app/.env`，restart 才真正生效。想临时覆盖仍可用 `docker run -e KEY=VALUE`，但同样只在创建时固化。


### 非 root 运行：写出的文件不用 sudo

`run.sh` 给两个 `docker run` 都加了 `--user "$(id -u):$(id -g)"`，`Dockerfile` 也设了 `USER 1000:1000` 作为镜像默认。bind mount 按**数字 uid** 判权，所以容器写出的 `logs/`、`data/` 文件属主就是宿主机当前用户，改、删、覆盖都不需要 `sudo`；常驻服务和一次性任务用同一个 uid，产出物属主一致。

| 场景 | 处理 |
| --- | --- |
| 普通用户执行 `./run.sh` | 默认对齐，开箱即用 |
| 部署账号 ≠ 登录账号 | `RUN_UID=1001 RUN_GID=1001 ./run.sh` |
| `sudo ./run.sh` | `id -u` 变 0，容器又回 root；请加 docker 组或显式给 `RUN_UID`（脚本会 WARN） |
| 项目目录本身属 root | `sudo chown -R "$(id -u):$(id -g)" .`（非 root 也需要目录可写） |
| 旧容器留下的 root 日志/Excel | `sudo chown -R "$(id -u):$(id -g)" logs data` |

启动前 `run.sh` 会自检项目根、`logs`、`data` 可写，并检查 `logs/data` 里是否还有非当前 uid 属主的文件；命中就报错并给出修复命令，避免容器起来后写日志失败却只被代码吞进日志。

**边界**：`--user` 用数字 uid 时容器内没有 passwd 条目，故额外传 `-e HOME=/tmp`（本项目不依赖 HOME）。默认 umask `022` → 新文件 `644`、目录 `755`，同机**其他**用户可读不可写；若确实要让任意系统用户都能改，得在宿主机目录上配默认 ACL 与 setgid（`sudo setfacl -R -d -m o::rwx logs data && sudo chmod -R g+s logs data`），这是宿主机目录权限策略，容器单方面给不了。非 root 进程也不能改镜像内系统文件或装包，但本项目只写 `/app/logs`、`/app/data`，不受影响。

```bash
./run.sh python -m xiaoxin_work_order.cli.app --mode batch --start 2026-04-01 --end 2026-04-28
./run.sh python -m xiaoxin_work_order.cli.generate_qa --start 2026-04-01 --end 2026-04-28
./run.sh python -m xiaoxin_work_order.cli.verify_env
```

本机（不用容器）：

```bash
python -m xiaoxin_work_order.cli.verify_env                          # 5 项全查
python -m xiaoxin_work_order.cli.verify_env --skip-model --skip-api  # 只查 3 个数据库
python -m xiaoxin_work_order.cli.app --mode daily                    # 只跑昨天一次
python tests/smoke_test.py                                # 非流式 / --stream 流式
python tests/test_daily_retry.py                          # 就绪探测 / 当日重试（离线，8 例）
python tests/check_prompts.py                             # 输出「全部一致：9 段提示词 + 25 条映射」
```

`xiaoxin_work_order.cli.verify_env` 每项输出 `[PASS]` / `[FAIL]`，倒数第二行输出「汇总：N 项，通过 M，失败 K」，最后一行输出说明文案，全部通过时退出码为 0、否则为 1；`tests/smoke_test.py` 末行输出 `ALL_TESTS_PASSED (stream=true|false)`。

`xiaoxin_work_order.cli.verify_env` 全部只读，不写业务表；`smoke_test.py` 起本地伪造模型服务，`test_daily_retry.py` 全部注入 fake 取数/入库/时钟，都不碰真实数据库与接口。

---

## 九、安全与维护提示

- `.env` 随项目交付且填有真实凭据；**生产环境建议把 `.env` 从版本库移除**（bind mount 仍需宿主机上有这份文件），`.env.example` 可作为脱敏模板。注意 `Dockerfile` 的 `COPY . .` 会把构建时的 `.env` 打进镜像层，运行时虽被 bind mount 覆盖、不会生效，但镜像层里仍可取到旧凭据；敏感环境可把 `.env` 加进 `.dockerignore` 后重新 build。若改用 `docker -e` / 密钥管理注入，则改值需重建容器，与「restart 即生效」二选一。
- 容器以 `--user "$(id -u):$(id -g)"` 非 root 运行，`logs/` / `data/` 产物属主为宿主机当前用户，无需 `sudo` 即可改删；非 root 也意味着容器内不能装包或改系统文件，改 `requirements.txt` 仍需重新 build 镜像。
- `.idea/` 是编辑器元数据，当前仓库中仍保留该目录；`.gitignore` / `.dockerignore` 仍保留忽略规则，防止再次提交。
- 改提示词 → 改 `prompts/*.txt` 并同步 `tests/source_snapshot.json`；改表结构/固定值 → 改 `xiaoxin_work_order/db/` 对应文件并同步 `xiaoxin_work_order/db/README.md` 的示例。
