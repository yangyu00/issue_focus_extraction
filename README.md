# issue_focus_extraction（重构版）

工单解析入库 + QA 知识生成。本次按三项要求做了合并与去 Dify 改造。

## 一、取数校验（按约定顺序，命中即停）

| 项 | 结果 |
| --- | --- |
| 地址 1 | `https://codeload.github.com/yangyu00/issue_focus_extraction/tar.gz/refs/heads/master` |
| HTTP | 200（GET 下载完整包体，36628 字节） |
| 包体校验 | `gzip -t` 通过；解压后有 **25 个文件** |
| 结论 | **地址 1 命中即停**，未尝试地址 2；全程做包体校验，未用 HEAD、网页内容、SNAPSHOT 或推测补全 |

> 沙盒不允许回填文件 mtime，`tar` 首次会报 `Cannot utime` 警告；用 `tar -m` 关闭 mtime 回填后，按「解压后有文件」判据复核为成功。地址 2 未被使用。

## 二、三个功能与运行方式

| 功能 | 命令 | 说明 |
| --- | --- | --- |
| 1. 每日定时处理 | `python app.py` | 默认：启动时先跑昨天，然后每天 `01:00` 跑昨天，常驻调度 |
| 2. 手动批量处理 | `python app.py --mode batch --start 2026-04-01 --end 2026-04-28` | 按天补跑，原 `app_batch.py` 能力 |
| 3. 手动生成 QA | `python generate_qa.py --start 2026-04-01 --end 2026-04-28` | 基于已入库结果聚类生成标准问答对 |

补充用法：

```bash
python app.py --mode daily                       # 只跑昨天一次
python app.py --mode date --date 2026-04-01       # 跑指定单日
python app.py --mode batch --start ... --end ... --at 02:00 --no-run-now
python generate_qa.py --start ... --end ... --qa-excel          # 额外导出 QA Excel
python generate_qa.py --enrich-excel ./data/包月费问题.xlsx     # 反查 GBase 明细（原 gbase_excel_query 的手动工具）
```

## 三、目录结构

```
app.py                  # 合并后的单入口：每日调度 / 批量补跑 / 单日
generate_qa.py          # QA 生成（GBase 查询、sql_query 逻辑、聚类、写库、Excel 全在本文件）
config.py               # 集中配置，支持环境变量覆盖
utils.py                # loguru 日志
llm_client.py           # OpenAI 兼容 chat/completions 直连客户端（限流/重试/流式/清洗）
prompt_loader.py        # 本地提示词加载与占位符注入
issue_workflow.py       # yml 工作流的直连编排（8 字段提取 + 映射链）
mappings.py             # yml 两个「映射」代码节点的分类字典（自动逐字转写）
prompts/                # 全部提示词，纯文本维护
  device.txt  business.txt  subclass.txt  symptom.txt
  symptom_detail.txt  cause.txt  branch_actions.txt  vendor_actions.txt
  qa_cluster.txt
小信智能体摘要提取.yml  # 仅作历史备份/对照，运行时不再读取，也不再调用 Dify
requirements.txt
```

已删除：`app_batch.py`（并入 app.py）、`dify.py`（不再调用）、`gbase_excel_query.py` + `sql_query.py`（并入 generate_qa.py）、`model.py`（替换为 llm_client.py + prompts/qa_cluster.txt）、`CLEANUP_NOTES.md`（旧清理说明已过时）。

## 四、工作流：从 Dify 改为直连模型

原 yml 的结构与现在的对应关系：

```
开始(sys.query = 标题/描述/分公司处理意见/厂商处理意见)
  ├─→ 设备 LLM ─────────────────────────────────┐
  ├─→ 业务 LLM ─→ [映射] → 二级分类 LLM ─→ [映射2] → 故障现象 LLM ─┤
  ├─→ 故障原因 LLM ─────────────────────────────┤
  ├─→ 分公司操作 LLM ───────────────────────────┼→ [代码执行] → 直接回复
  ├─→ 厂商操作 LLM ─────────────────────────────┤   （清洗 + 组装 JSON）
  └─→ 故障详情 LLM ─────────────────────────────┘
```

- 两个「映射」代码节点 → `mappings.py` 的字典查表（业务→二级分类候选、二级分类→焦点问题候选），**不消耗模型调用**。
- 8 个 LLM 节点 → `llm_client.chat()` 直连 `LLM_URL`，6 个独立字段并发，业务链串行；提示词逐字来自 yml，放在 `prompts/*.txt`。
- 「代码执行」节点 → `issue_workflow.clean_field()`：去换行、去 `<think>`、去所有空格，按原顺序组装 8 字段 JSON。
- 「直接回复」节点 → `run_extraction()` 返回该 JSON 字符串，`app.py` 的 `process_model_output_for_db()` 无需改动。
- `/no_think` 后缀按 yml 原样保留在各提示词里。

输出字段不变：`device / business / subclass / symptom / symptom_detail / cause / branch_actions / vendor_actions`。

## 五、配置与环境变量

`config.py` 保留原默认值，全部可用同名环境变量覆盖（建议生产环境外置密钥）：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` / `DB_NAME` | `134.84.22.233:6306` / `root` / … | app.py 入库 MySQL |
| `QA_DB_HOST` / `QA_DB_PORT` / … | `172.21.224.71:6306` | generate_qa 读写 MySQL |
| `GBASE_HOST` / `GBASE_PORT` / `GBASE_USER` / `GBASE_PASSWORD` / `GBASE_DB` | `134.85.16.139:5258` / `busiopera` / `opera` | GBase 明细查询 |
| `BACKEND_API_URL` | `http://134.84.22.233:6308/xxa/queryListByTime` | 取数接口 |
| `LLM_URL` / `LLM_API_KEY` | `http://134.84.255.13:23471/v1/chat/completions` | 直连模型服务 |
| `LLM_MODEL` / `QA_LLM_MODEL` | `CXY-Qwen3-32B` / `cxy-deepseek-v3-0324` | 提取 / QA 聚类分别用 |
| `LLM_QPS` / `LLM_MAX_RETRIES` / `LLM_TIMEOUT` / `LLM_STREAM` | `2` / `3` / `120` / `false` | 限流、重试、超时、流式开关 |
| `MAX_WORKERS` / `FIELD_WORKERS` | `1` / `6` | 工单级 / 字段级并发 |
| `POST_CALL_SLEEP` / `BATCH_DAY_SLEEP` / `SCHEDULE_TIME` | `5` / `2` / `01:00` | 节奏控制 |

## 六、重构时一并处理的几处历史问题

1. **DB 端口不一致**：原 `app.py` 用 `8086`，`app_batch.py` / `generate_qa.py` / `sql_query.py` 用 `6306`；合并后默认 `6306`，如线上是 8086 设 `DB_PORT=8086`。
2. **DB 地址不一致**：`generate_qa.py` 原来指向 `172.21.224.71`，单独保留为 `QA_DB_*`，避免误写库。
3. **硬编码密钥**：Dify API Key、模型 API Key、数据库密码原先散落在代码里，现统一在 `config.py` 并支持环境变量覆盖。
4. **`generate_qa.py` 的假数据覆盖**：原代码在 `as_completed` 里把模型返回的 `qa_list` 强行替换为两条写死测试数据，且循环内写库一次、结尾又全量写库一次（重复入库）。已删除假数据，改为结尾统一写库一次。
5. **提示词内嵌**：`model.py` 的 QA 提示词与 Dify yml 的 8 段提示词全部迁到 `prompts/`，改提示词不用改 Python 代码。
6. `generate_qa.py` 的日期不再写死，改由 `--start/--end` 传入。

## 七、原 ReadMe 要点（保留）

- 调用后端（魏玉磊）接口：输入是时间，返回 `taskOid`、`lastTime`、`fld_faultcontent`（报障内容）、`fld_column_2`（分公司处理意见）、`fld_column_4`（厂商处理意见）、`fld_question_type_province`（省公司确认问题类型）；输入时间取 `lastTime`、`start_time`、`end_time` 中的最新日期。
- 入库字段：`business` 业务、`device` 设备、`subclass` 二级分类、`symptom` 故障现象、`symptom_detail` 故障详情、`cause` 故障原因、`branch_actions` 分公司操作、`vendor_actions` 厂商操作、`create_time` 入库时间、`response_time` 模型响应耗时、`error_msg` 失败原因。
- `generate_qa.py` 在 app 入库结果的基础上，生成某段时间的 QA 知识。

## 八、部署与上线自检

### 8.1 补齐 Docker 部署（原 ReadMe 的三处问题）

原 `ReadMe.md` 给的两条命令有三个落地障碍，本次已补齐：

1. `docker build -t xiaoxin:v1 .` 与 `docker run ... xiaoxin_qa:v1` **镜像名不一致**，run 会直接报找不到镜像。
2. 仓库里**根本没有 Dockerfile**。
3. `-p 8887-8889:8887-8889` 映射了端口，但重构后（以及原版）代码都是 CLI 常驻进程 + `schedule` 循环，**不监听任何 HTTP 端口**，端口映射无效。如需对外提供触发接口，要另加一层 Web 服务，不在本项目范围内。

现在可直接构建运行：

```bash
cp .env.example .env && vi .env          # 四项 CHANGE_ME 必须改成真实值
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

`config.py` 会在启动时可选加载项目根目录的 `.env`（自实现，未引入 python-dotenv 依赖）。优先级：**显式 `export` 的环境变量 > `.env` > `config.py` 内置默认值**。`.env` 已被 `.gitignore` 排除，仓库只提交 `.env.example` 模板。

> ⚠️ 密钥安全：原公开仓库里 `dify.py` 的 Dify API Key、`model.py` / `app.py` 的数据库密码都是明文硬编码。重构版虽保留默认值以兼容开箱运行，但**这些凭据应视为已泄露并全部轮换**，生产环境一律通过 `.env` / 环境变量注入，不要再依赖代码里的默认值。

### 8.3 上线自检（补上「未连真实环境」这一环）

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

- **`smoke_test.py`**：本地起一个伪造的 OpenAI 兼容服务，验证 8 字段提取 → 两个映射节点注入候选类别 → `<think>`/空格清洗 → QA 聚类 JSON 解析 → `app.safe_run_model` 元数据。不碰真实数据库与接口。
- **`check_prompts.py`**：把 `prompts/*.txt` 与 `mappings.py` 跟 `tests/source_snapshot.json` 逐字比对。快照由 yml 的 8 个 LLM 节点、2 个映射代码节点以及 `model.py` 的 QA 提示词导出，改过提示词或映射表后跑一次就能发现误改；**不需要 PyYAML**。

两个脚本都可在 CI 里直接跑，任一不一致退出码为 1。

## 十、本轮续做新增文件

```
.env.example               全量环境变量模板（敏感项 CHANGE_ME）
.gitignore                 .env / logs / data / 缓存
Dockerfile                 python:3.11-slim + requirements，CMD 跑 app.py
.dockerignore
docker-compose.yml         常驻定时服务 + run --rm 临时任务用法
verify_env.py              只读五连自检
tests/smoke_test.py        离线端到端回归（非流式 / 流式）
tests/source_snapshot.json  yml / model.py 逐字导出的提示词与映射基线
tests/check_prompts.py     零依赖基线比对
config.py                  增加 .env 加载（优先级：export > .env > 默认值）
```

核心业务代码（`app.py` / `generate_qa.py` / `issue_workflow.py` / `llm_client.py` / `prompts/`）本轮**未改动逻辑**，仅补齐部署、自检、测试与配置外置；`小信智能体摘要提取.yml` 保留为历史对照，运行时不读取。
