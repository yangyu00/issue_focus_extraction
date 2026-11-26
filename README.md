# issue_focus_extraction

工单解析入库 + QA 知识生成。

## 二、三个功能与运行方式

| 功能 | 命令 | 说明 |
| --- | --- | --- |
| 1. 每日定时处理 | `python app.py` | 默认：启动时先跑昨天，然后每天 `01:00` 跑昨天，常驻调度 |
| 2. 手动批量处理 | `python app.py --mode batch --start 2026-04-01 --end 2026-04-28` | 按天补跑 |
| 3. 手动生成 QA | `python generate_qa.py --start 2026-04-01 --end 2026-04-28` | 基于已入库结果聚类生成标准问答对 |

补充用法：

```bash
python app.py --mode daily                       # 只跑昨天一次
python app.py --mode date --date 2026-04-01       # 跑指定单日
python app.py --mode batch --start ... --end ... --at 02:00 --no-run-now
python generate_qa.py --start ... --end ... --qa-excel          # 额外导出 QA Excel
python generate_qa.py --enrich-excel ./data/包月费问题.xlsx     # 反查 GBase 明细
```

## 三、目录结构

```
app.py                  # 单入口：每日调度 / 批量补跑 / 单日
generate_qa.py          # QA 生成（GBase 查询、按 id 反查、聚类、写库、Excel 全在本文件）
.env                    # 本地运行配置，密码 / API Key 已按实际环境照实填写
config.py               # 集中配置，启动时加载 .env，也支持环境变量覆盖
utils.py                # loguru 日志
llm_client.py           # OpenAI 兼容 chat/completions 直连客户端（限流/重试/流式/清洗）
prompt_loader.py        # 本地提示词加载与占位符注入
issue_workflow.py       # 8 字段提取 + 映射链的直连编排
mappings.py             # 分类映射字典 MAPPING（由 tests/check_prompts.py 校验）
prompts/                # 全部提示词，纯文本维护
  device.txt  business.txt  subclass.txt  symptom.txt
  symptom_detail.txt  cause.txt  branch_actions.txt  vendor_actions.txt
  qa_cluster.txt
requirements.txt
```

## 四、工作流：8 字段提取

字段提取的调用结构：

```
开始(sys.query = 标题/描述/分公司处理意见/厂商处理意见)
  ├─→ 设备 LLM ─────────────────────────────────┐
  ├─→ 业务 LLM ─→ [映射] → 二级分类 LLM ─→ [映射2] → 故障现象 LLM ─┤
  ├─→ 故障原因 LLM ─────────────────────────────┤
  ├─→ 分公司操作 LLM ───────────────────────────┼→ [代码执行] → 直接回复
  ├─→ 厂商操作 LLM ─────────────────────────────┤   （清洗 + 组装 JSON）
  └─→ 故障详情 LLM ─────────────────────────────┘
```

- `mappings.py` 的 `MAPPING` 字典查表（业务→二级分类候选、二级分类→焦点问题候选），**不消耗模型调用**。
- 8 个字段由 `llm_client.chat()` 直连 `LLM_URL` 提取：6 个独立字段并发，业务链串行；提示词放在 `prompts/*.txt`。
- `issue_workflow.clean_field()` 去换行、去 `<think>`、去所有空格，按 `OUTPUT_KEYS` 顺序组装 8 字段 JSON。
- `run_extraction()` 返回该 JSON 字符串，由 `app.py` 的 `process_model_output_for_db()` 解析为入库字段。
- `/no_think` 后缀保留在除 `device.txt` 外的 8 段提示词里；`device.txt` 不含该后缀。

输出字段：`device / business / subclass / symptom / symptom_detail / cause / branch_actions / vendor_actions`。

## 五、配置与环境变量

`config.py` 在启动时加载项目根目录的 `.env`；全部配置也可用同名环境变量覆盖。未提供时取 `config.py` 内置默认值，`.env` 文件不存在则跳过加载：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` | `134.84.22.233:6306` / `root` / … | app.py 入库 MySQL |
| `QA_DB_HOST` / `QA_DB_PORT` / … | `172.21.224.71:6306` | generate_qa 读写 MySQL |
| `GBASE_HOST` / `GBASE_PORT` / `GBASE_USER` / `GBASE_PASSWORD` / `GBASE_DB` | `134.85.16.139:5258` / `busiopera` / `opera` | GBase 明细查询 |
| `BACKEND_API_URL` | `http://134.84.22.233:6308/xxa/queryListByTime` | 取数接口 |
| `LLM_URL` | `http://134.84.255.13:23471/v1/chat/completions` | 直连模型服务 |
| `LLM_MODEL` / `QA_LLM_MODEL` | `CXY-Qwen3-32B` / `cxy-deepseek-v3-0324` | 提取 / QA 聚类分别用 |
| `LLM_QPS` / `LLM_MAX_RETRIES` / `LLM_TIMEOUT` / `LLM_STREAM` | `2` / `3` / `120` / `false` | 限流、重试、超时、流式开关 |
| `MAX_WORKERS` / `FIELD_WORKERS` | `1` / `6` | 工单级 / 字段级并发 |
| `POST_CALL_SLEEP` / `BATCH_DAY_SLEEP` / `SCHEDULE_TIME` | `5` / `2` / `01:00` | 节奏控制 |
| `QA_DB_USER` / `QA_DB_PASSWORD` / `QA_DB_NAME` / `LLM_API_KEY` | 取值见 `config.py` 内置默认值或 `.env`（属外部依赖，由部署方／模型服务责任人提供） | 数据库与模型鉴权，取值不写入文档 |
| `LLM_TEMPERATURE` | `0.1` | 模型采样温度 |

补充运行约束与外部依赖：

- 进程与上游：常驻进程只有 `app.py`（`schedule` 循环，启动时先跑昨天，再按 `SCHEDULE_TIME` 每日执行）；`generate_qa.py` / `verify_env.py` / `tests/*` 为一次性任务，由 `docker compose run --rm` 临时启动。运行前需可达：后端取数接口 `BACKEND_API_URL`、app 入库 MySQL（`MYSQL_CONFIG`）、QA MySQL（`QA_MYSQL_CONFIG`）、GBase（`GBASE_CONFIG`）、模型服务（`LLM_URL`）。上述地址与凭据的获取渠道／责任人：由部署方提供（属外部依赖）。
- 运行基线：镜像基于 `python:3.11-slim`（`Dockerfile`），容器内 `TZ=Asia/Shanghai`；依赖版本见 `requirements.txt`。并发为两级：`MAX_WORKERS`（默认 1）控制工单级并发，`FIELD_WORKERS`（默认 6）控制单条工单的字段级并发；模型侧由 `LLM_QPS` 全局限流。

## 七、接口与入库字段

- 后端取数接口由 `app.py` 的 `fetch_data_from_api(start_time, end_time)` 以 `startTime` / `endTime` 调用，日期由 `--mode` 参数或每日调度传入；响应需满足 `code == 200` 且 `success` 为真，`result` 为工单列表。工单字段在 `app.py` 中按 `taskOid` / `lastTime` / `taskTitle` / `fldFaultcontent` / `fldColumn2` / `fldColumn4` 读取；GBase `xiaoxin` 表使用 `TASK_OID` / `TASK_TITLE` / `fld_faultcontent` / `fld_column_2`。
- 入库字段：`business` 业务、`device` 设备、`subclass` 二级分类、`symptom` 故障现象、`symptom_detail` 故障详情、`cause` 故障原因、`branch_actions` 分公司操作、`vendor_actions` 厂商操作、`create_time` 入库时间、`response_time` 模型响应耗时、`error_msg` 失败原因。
- `generate_qa.py` 在 app 入库结果的基础上，生成某段时间的 QA 知识。
- 入库分流：`app.py` 按 `fldColumn4` 是否有效分流——有效数据写入 `xxa_issues_records`（`key_issues` 取 `symptom`）、`xxa_complaint_word_cloud`（`key_word` 取 `symptom`）、`xxa_bomc_qa_records`；无厂商处理意见的数据写入 `xxa_bomc_qa_non_vendor`。写入前按 `task_oid` 删除同名旧记录；四张表名见 `verify_env.py` 的 `APP_TABLES`。
- QA 生成数据契约：`generate_qa.py` 读取 `xxa_bomc_qa_non_vendor`，过滤 `symptom_detail == "其他"` 与 `branch_actions` 的空操作数组，按 `symptom` 分组、每 `CHUNK_SIZE=400` 条分批聚类，结果统一写入 `xxa_city_qa_knowledge`（列顺序 `question, answer, knowledge_source, work_id, name, city, city_text, task_oids`）。

## 八、部署与上线自检

### 8.1 Docker 部署

当前代码是 CLI 常驻进程（`schedule` 循环），不监听任何 HTTP 端口，容器不映射端口。如需对外提供触发接口，要另加一层 Web 服务，不在本项目范围内。

现在可直接构建运行：

```bash
# .env 已随项目提供且填好真实值；只确认 DB_PORT 是否符合你的库
docker build -t xiaoxin:v1 .
docker run -dit --name xiaoxin_qa \
  --env-file .env \
  -v $(pwd)/logs:/app/logs -v $(pwd)/data:/app/data \
  xiaoxin:v1

# 或用 compose（镜像名一致、日志与 data 挂载已配好）
docker compose up -d
```

批量补跑 / QA 生成 / 自检用临时容器，不影响常驻服务：

```bash
docker compose run --rm xiaoxin python app.py --mode batch --start 2026-04-01 --end 2026-04-28
docker compose run --rm xiaoxin python generate_qa.py --start 2026-04-01 --end 2026-04-28
docker compose run --rm xiaoxin python verify_env.py
```

### 8.2 配置外置

`config.py` 会在启动时加载项目根目录的 `.env`（自实现，未引入 python-dotenv 依赖）。优先级：**显式 `export` 的环境变量 > `.env` > `config.py` 内置默认值**。

`.env` 已按个人局域网的实际环境把数据库密码、GBase 密码、模型 API Key **照实填好**，随项目一起交付；`.gitignore` / `.dockerignore` 均不排除它，构建镜像时会被 `COPY . .` 带进去，`docker-compose.yml` 另用 `env_file: .env` 注入，两条通道取值一致，开箱即跑。

### 8.3 上线自检

```bash
python verify_env.py                          # 5 项全查
python verify_env.py --skip-model --skip-api  # 只查 3 个数据库
```

逐项检查 app 入库 MySQL（顺带核对 4 张目标表是否存在）、QA MySQL、GBase、后端取数接口（用昨天日期试查）、直连模型服务。全部**只读**，不写任何业务表；模型项会发起 1 次极短调用。任一失败退出码为 1，可直接接进发布流程。内网不通时脚本不会卡死（连接 5s / 接口 10s 超时）。

## 九、回归测试

```bash
python tests/smoke_test.py            # 非流式
python tests/smoke_test.py --stream   # 流式 SSE
python tests/check_prompts.py         # 提示词 / 映射表基线比对（零依赖）
```

- **`smoke_test.py`**：本地起一个伪造的 OpenAI 兼容服务，验证 8 字段提取 → `MAPPING` 注入候选类别 → `<think>`/空格清洗 → QA 聚类 JSON 解析 → `app.safe_run_model` 元数据。不碰真实数据库与接口。
- **`check_prompts.py`**：把 `prompts/*.txt` 与 `mappings.py` 跟 `tests/source_snapshot.json` 逐字比对；基线文件的 `source` 字段记录了导出来源，改过提示词或映射表后跑一次就能发现误改；**不需要 PyYAML**。
- **`verify_env.py`**：全部检查通过时退出码为 0，末行输出 `汇总：N 项，通过 N，失败 0`；任一失败退出码为 1。
- **`smoke_test.py`**：全部断言通过时末行输出 `ALL_TESTS_PASSED`。
- **`check_prompts.py`**：全部一致时输出 `全部一致：9 段提示词 + 25 条映射`，退出码为 0。

两个脚本都可在 CI 里直接跑，任一不一致退出码为 1。
