# 项目清理说明（issue_focus_extraction）

> 修订：按实际运行情况，`generate_qa.py` 也会单次手动运行，因此它与它的依赖链一并保留。本文按「三个入口」重新计算依赖闭包。

## 一、取数校验（按约定顺序，命中即停）

| 项 | 结果 |
| --- | --- |
| 地址 1 | `https://codeload.github.com/yangyu00/issue_focus_extraction/tar.gz/refs/heads/master` |
| HTTP | 200 |
| 包体校验 | `gzip -t` 通过；`tar -tzf` 共 68 个条目，解压后 56 个文件 |
| 结论 | 地址 1 命中即停，**未尝试**地址 2；全程 GET 下载包体校验，未用 HEAD、网页内容、SNAPSHOT 或推测补全 |

## 二、三个入口与依赖闭包

```
app.py           每日定时解析入库（入口 1）
app_batch.py     指定时段批量补跑（入口 2）
generate_qa.py   手动单次运行，生成 QA 知识（入口 3）★本次加回
```

闭包计算（逐个解析 `import` / `from ... import`，递归展开）：

| 文件 | 本地依赖 | 说明 |
| --- | --- | --- |
| `app.py` | `config`, `dify`, `utils` | 入口 1 |
| `app_batch.py` | `config`, `dify`, `utils` | 入口 2 |
| `generate_qa.py` | `config`, `gbase_excel_query`, `model`, `utils` | 入口 3 |
| `gbase_excel_query.py` | `sql_query` | 提供 `query_gbase_by_task_oids` |
| `sql_query.py` | — | 提供 `get_records_by_ids` |
| `model.py` | — | 提供 `run_model`，直连大模型（prompt 内嵌在代码里，不读外部文件） |
| `dify.py` | `utils` | Dify 工作流请求 |
| `utils.py` | `config` | loguru 日志 |
| `config.py` | — | 日志路径常量；运行时自动创建 `logs/INFO`、`logs/ERROR` |

**共 9 个 .py，全部保留。**另外保留 `requirements.txt`、`ReadMe.md`。

### 几处容易误判、已逐一核查的点

1. `config.py` 的 `GENERATE_QA_INFO_LOG_NAME` / `GENERATE_QA_ERROR_LOG_NAME` **不是残留**：`generate_qa.py` 顶部 `setup_logging(GENERATE_QA_INFO_LOG_NAME, GENERATE_QA_ERROR_LOG_NAME)` 在用。
2. `gbase_excel_query.py` 里的 `./data/包月费问题.xlsx` 只出现在它的 `if __name__ == "__main__"` 测试块；`generate_qa.py` 只调用 `query_gbase_by_task_oids()`，不触发 `main()`，仓库本身也没有 `data/` 目录 → 不需要预置输入 Excel。
3. `generate_qa.py` 输出 `data/gbase_*.xlsx` 前会 `os.makedirs("data")` 自动建目录 → 不需要预置输出目录。
4. `model.py` 的提示词是内嵌字符串，**不读取 `文档/prompt.txt`**；三个入口均无任何对 `文档/` 的读取引用。
5. `read_qa_excel.py` 读取 `qa_*.xlsx`，但没有任何保留文件 import 它（`generate_qa.py` 当前把 QA 写库 `save_to_database`，Excel 输出段已被注释）→ 判定为无关独立工具。若你的手动流程里会单独用它读导出的 QA Excel，可自行加回。

## 三、保留清单（12 个文件）

```
app.py  app_batch.py  generate_qa.py
config.py  dify.py  utils.py  model.py
gbase_excel_query.py  sql_query.py
requirements.txt  ReadMe.md  CLEANUP_NOTES.md（本说明）
```

## 四、删除清单（45 个文件）

原始 56 个 − 保留 11 个原文件 = 删除 45 个。

### 1. 闭包外代码文件（4 个）
| 文件 | 删除原因 |
| --- | --- |
| `main.py` | 旧版离线 Excel + model 总结链路，三个入口都不引用 |
| `gbase_query.py` | 与 `gbase_excel_query.py` 功能重复的独立查库脚本，无被引用 |
| `read_qa_excel.py` | 读 `qa_*.xlsx` 的独立工具，无被引用（见上节第 5 点） |
| `test.py` | 4 行调试片段 |

### 2. 其他（41 个）
- `.idea/` 12 个：IDE 数据源/部署/检查配置，纯本地残留
- `logs/` 12 个：2026-04-25/28/29 历史 INFO、ERROR 日志，`config.py` 运行时自动重建目录
- `文档/` 16 个：技术方案 docx、排期 xlsx、CRM 查证 pptx、投诉热点索引表、各类词表 txt；三个入口均不读取
- `小信智能体摘要提取.yml` 1 个：Dify 工作流导出备份，非运行依赖（`generate_qa.py` 走 `model.py` 直连大模型，不走 Dify）

## 五、requirements.txt（按保留代码的真实 import）

加回 `openpyxl==3.1.5`：`generate_qa.py` 的 `df_gbase.to_excel(...xlsx)` 与 `gbase_excel_query.py` 的 `read_excel/to_excel` 都需要它。
加回 `json-repair==0.58.0`：`generate_qa.py` 顶层裸 `import json_repair`，缺失会直接 ImportError。
移除 `xlrd`、`xlwt`：保留代码中没有 `.xls` 读写，只有 `.xlsx`。
`pymysql` 保留：三个入口的数据库连接串均为 `mysql+pymysql://...`。`numpy` 保留：pandas 2.2.3 运行依赖，按原版本不动。

## 六、校验结果

- 清理后目录 12 个文件；9 个 `.py` 全部通过 `compile()` 语法校验。
- 以 `app`、`app_batch`、`generate_qa` 为起点重算闭包，9 个模块全部在目录内，无未解析的本地 import；无 `__import__` / `importlib` 动态导入。
- 56 − 11 = 45，与删除清单分项（4 + 12 + 12 + 16 + 1）一致。

## 七、重构前建议留意（本次未改动任何代码内容）

1. `app.py` 与 `app_batch.py` 479/478 行，差异仅 DB 端口（8086 / 6306）与 `__main__` 启动方式，可合并单入口 + 参数。
2. 三个入口各有自己的 `DB_CONFIG`，地址互不相同（134.84.22.233:8086 / :6306、172.21.224.71:6306），且数据库账号密码、`dify.py` 的 API Key、`model.py` 的 API Key 全部硬编码 → 建议外置配置。
3. `generate_qa.py` 文件头注释自述「`run_model` 已注释，当前只负责取数 + 查 GBase + 导出 Excel」，但代码里 109–123 行仍保留着调用 `run_model` 的构造逻辑；`__main__` 里日期写死为 2026-04-01 ~ 2026-04-28，手动跑前需改。重构时值得先理清哪条路径是当前真实行为。
4. `dify.py` 的 `__main__` 测试块、`config.py` 里注释掉的旧 HOST/PORT、两个入口里注释掉的场景代码，属文件内残留，留待重构阶段处理。
5. `ReadMe.md` 原样保留，其中 `generate_qa.py` 的描述现在重新有效；但 docker 打包/启动命令引用的 Dockerfile 并不在仓库内。

## 八、落地到原仓库（本次只清理本地副本，未改动 GitHub）

```bash
git rm -r -- .idea logs 文档
git rm -- main.py gbase_query.py read_qa_excel.py test.py 小信智能体摘要提取.yml
```
