# 项目清理说明（issue_focus_extraction）

## 一、取数校验（按约定顺序，命中即停）

| 项 | 结果 |
| --- | --- |
| 地址 1 | `https://codeload.github.com/yangyu00/issue_focus_extraction/tar.gz/refs/heads/master` |
| HTTP | 200 |
| 包体校验 | `gzip -t` 通过；`tar -tzf` 共 68 个条目，解压后 56 个文件 |
| 结论 | 地址 1 命中即停，**未尝试**地址 2；全程使用 GET 下载包体校验，未用 HEAD、网页内容、SNAPSHOT 或推测补全 |

## 二、保留的文件（7 个原文件 + 本说明）

保留依据：以实际在用的两个入口做依赖闭包分析，缺任一文件都会 ImportError。

```
app.py          每日定时解析入库（入口）
app_batch.py    指定时间段批量补跑（入口）
├── config.py       日志路径常量；运行时自动创建 logs/INFO、logs/ERROR
├── dify.py         Dify 工作流请求
│   └── utils.py    loguru 日志（dify.py 的 from utils import logger）
└── utils.py
    └── config.py   from config import *
```

另保留 `requirements.txt`（按保留代码的真实 import 精简，见第四节）、`ReadMe.md`（原样保留）。

**保留文件之间的本地 import 全部可解析，5 个 .py 全部通过语法编译校验。**

## 三、删除清单（共 49 个文件）

### 1. 无用代码文件（9 个）
| 文件 | 原用途 | 删除原因 |
| --- | --- | --- |
| `main.py` | 从 Excel 读列、调用 model.py 做总结 | 旧版离线处理链路，与每日入库链路无关 |
| `model.py` | 直连大模型 `run_model` | 现链路统一走 dify.py，仅被 main.py / generate_qa.py 引用 |
| `generate_qa.py` | 基于入库结果生成 QA 知识 | ReadMe 自述的附加功能，实际未使用 |
| `gbase_query.py` | 按 taskOid 查 GBase | 仅 generate_qa.py 链路使用 |
| `gbase_excel_query.py` | 查 GBase 并导出 Excel | 仅 generate_qa.py 链路使用 |
| `sql_query.py` | 通用 SQL 查询 | 仅被 gbase_query.py / gbase_excel_query.py 引用 |
| `read_qa_excel.py` | 读取 `qa_*.xlsx` | 仅 generate_qa.py 链路使用 |
| `test.py` | 4 行调试片段 | 无功能 |
| `小信智能体摘要提取.yml` | Dify 工作流导出备份 | 非运行依赖，提示词在 Dify 服务端 |

### 2. IDE 配置 `.idea/`（12 个文件）
含数据源连接信息、部署配置、inspection 配置等，纯本地环境残留。

### 3. 历史日志 `logs/`（12 个文件）
2026-04-25 / 28 / 29 的 INFO、ERROR 日志。`config.py` 在运行时会自动重建 logs 目录，不需要预置历史日志。

### 4. 历史文档与词表 `文档/`（16 个文件）
技术方案 docx、排期 xlsx、CRM 查证 pptx、投诉热点索引表、各类提示词 txt 等。已逐文件核查：`app.py` / `app_batch.py` **没有任何**对这些文件的读取引用（取数来自后端 API，提示词在 Dify 服务端），故整体删除。

## 四、requirements.txt 精简

删除（仅被已删文件使用）：`xlrd`、`xlwt`、`openpyxl`、`json-repair`。
保留 `pymysql`：两个入口的数据库连接串为 `mysql+pymysql://...`，是 SQLAlchemy 的 MySQL 驱动，必须保留。
保留 `numpy`：pandas 2.2.3 的运行依赖，按原版本不动。

## 五、校验结果

- 清理后目录共 8 个文件：5 个 .py + `requirements.txt` + `ReadMe.md` + 本说明。
- 5 个 .py 全部通过 `compile()` 语法校验；所有本地 import（`config`、`dify`、`utils`）均能在目录内解析；无 `__import__` / `importlib` 动态导入。
- 原始 56 个文件 − 保留 7 个 = 删除 49 个，数量与清单一致。

## 六、重构前建议留意（本次未改动代码内容）

1. **app.py 与 app_batch.py 高度重复**：479 / 478 行，差异仅在 DB 端口（8086 / 6306）、`__main__` 启动方式（定时循环 / 直接 `run_batch_job("2025-11-01","2025-11-30")`）和一个 emoji。重构时可合并为单一入口 + 命令行参数。
2. **硬编码配置**：`app.py` / `app_batch.py` 内含数据库账号密码、后端 API 地址；`dify.py` 内含 API Key。建议外置到环境变量/配置文件。
3. **文件内残留**：`config.py` 中的 `GENERATE_QA_*` 日志常量、`dify.py` 的 `__main__` 测试块、两个入口里注释掉的场景代码，属文件内残留，留待重构阶段处理。
4. **ReadMe.md 已部分过时**：其中 docker 打包/启动命令引用的 Dockerfile 并不在仓库内，`generate_qa.py` 描述也随文件删除失效；"调用后端接口"与"入库字段"两段仍有效。
5. 本次只在本地副本上做清理并交付，**未改动 GitHub 仓库**。如需在原仓库落地：

```bash
git rm -r -- .idea logs 文档
git rm -- main.py model.py generate_qa.py gbase_query.py gbase_excel_query.py \
          sql_query.py read_qa_excel.py test.py 小信智能体摘要提取.yml
```
