# -*- coding: utf-8 -*-
"""集中配置。

所有连接信息、运行参数集中在此，支持用同名环境变量覆盖，便于 docker / 生产环境外置密钥。
默认值保持与重构前各文件一致；若线上 DB 端口/地址不同，直接设环境变量即可，不必改代码。
"""
import os
from urllib.parse import quote_plus

PROJECT_PATH = os.path.dirname(os.path.abspath(__file__))

# ================= 日志 =================
BASE_LOG_PATH = os.path.join(PROJECT_PATH, "logs")
INFO_LOG_PATH = os.path.join(BASE_LOG_PATH, "INFO")
ERROR_LOG_PATH = os.path.join(BASE_LOG_PATH, "ERROR")
INFO_LOG_NAME = "xiaoxin-info"
ERROR_LOG_NAME = "xiaoxin-error"
GENERATE_QA_INFO_LOG_NAME = "generate_qa_knowledge-info"
GENERATE_QA_ERROR_LOG_NAME = "generate_qa_knowledge-error"

for _dir in (BASE_LOG_PATH, INFO_LOG_PATH, ERROR_LOG_PATH):
    os.makedirs(_dir, exist_ok=True)


def _load_dotenv():
    """可选加载项目根目录的 .env（不引入额外依赖）。

    优先级：显式导出的环境变量 > .env > config.py 内置默认值。
    .env 随项目一起交付（个人局域网部署，凭据按实际环境照实填写）。
    """
    path = os.path.join(PROJECT_PATH, ".env")
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()

# ================= 环境变量读取 =================
def _s(name, default):
    return os.environ.get(name, default)


def _i(name, default):
    return int(os.environ.get(name, default))


def _f(name, default):
    return float(os.environ.get(name, default))


def _b(name, default):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


# ================= MySQL：app.py 每日 / 批量入库 =================
# 注意：原 app.py 用 134.84.22.233:8086，原 app_batch.py 用 134.84.22.233:6306。
# 合并为单入口后默认取 6306；若线上实际是 8086，设 DB_PORT=8086 即可。
MYSQL_CONFIG = {
    "host": _s("DB_HOST", "134.84.22.233"),
    "port": _i("DB_PORT", 6306),
    "user": _s("DB_USER", "root"),
    "password": _s("DB_PASSWORD", "Nttslgkpt*2024"),
    "database": _s("DB_NAME", "ntt_xiaoxin_agent"),
    "charset": "utf8mb4",
}

# ================= MySQL：generate_qa 读取与写库 =================
# 原 generate_qa.py 指向 172.21.224.71:6306，与 app 不在同一地址，故单独保留一份。
QA_MYSQL_CONFIG = {
    "host": _s("QA_DB_HOST", "172.21.224.71"),
    "port": _i("QA_DB_PORT", 6306),
    "user": _s("QA_DB_USER", "root"),
    "password": _s("QA_DB_PASSWORD", "Nttslgkpt*2024"),
    "database": _s("QA_DB_NAME", "ntt_xiaoxin_agent"),
    "charset": "utf8mb4",
}

# ================= GBase（opera 库），原 gbase_excel_query.py =================
GBASE_CONFIG = {
    "host": _s("GBASE_HOST", "134.85.16.139"),
    "port": _i("GBASE_PORT", 5258),
    "user": _s("GBASE_USER", "busiopera"),
    "password": _s("GBASE_PASSWORD", "Busiopera#2025"),
    "db": _s("GBASE_DB", "opera"),
    "charset": "utf8",
}

# ================= 后端取数接口 =================
BACKEND_API_URL = _s("BACKEND_API_URL", "http://134.84.22.233:6308/xxa/queryListByTime")

# ================= 直连大模型（OpenAI 兼容 /v1/chat/completions） =================
# yml 里模型名是 CXY-Qwen3-32B；generate_qa 原来用 cxy-deepseek-v3-0324，二者分别可配。
LLM_URL = _s("LLM_URL", "http://134.84.255.13:23471/v1/chat/completions")
LLM_API_KEY = _s("LLM_API_KEY", "sk-PxwP10Ndr_vJZ-s2sbTE2A")
LLM_MODEL = _s("LLM_MODEL", "CXY-Qwen3-32B")
QA_LLM_MODEL = _s("QA_LLM_MODEL", "cxy-deepseek-v3-0324")
LLM_TEMPERATURE = _f("LLM_TEMPERATURE", 0.1)
LLM_TIMEOUT = _i("LLM_TIMEOUT", 120)
LLM_MAX_RETRIES = _i("LLM_MAX_RETRIES", 3)
LLM_QPS = _f("LLM_QPS", 2.0)          # 全局限流：每秒最多发起多少次请求
LLM_STREAM = _b("LLM_STREAM", False)  # 默认非流式；设 LLM_STREAM=true 可走 SSE

# ================= 运行参数 =================
MAX_WORKERS = _i("MAX_WORKERS", 1)              # 工单级并发（工作流内部另有字段级并发）
FIELD_WORKERS = _i("FIELD_WORKERS", 6)          # 单条工单 8 字段提取时的字段级并发
POST_CALL_SLEEP = _f("POST_CALL_SLEEP", 5)      # 原 safe_run_model 每条调用后的固定停顿
BATCH_DAY_SLEEP = _f("BATCH_DAY_SLEEP", 2)      # 批量任务每天之间的停顿
SCHEDULE_TIME = _s("SCHEDULE_TIME", "01:00")    # 每日定时执行时间
PROMPTS_DIR = os.path.join(PROJECT_PATH, "prompts")
DATA_DIR = os.path.join(PROJECT_PATH, "data")


def mysql_url(cfg):
    """由配置字典生成 SQLAlchemy 连接串（对账号密码做 URL 编码）。"""
    return (
        f"mysql+pymysql://{quote_plus(str(cfg['user']))}:{quote_plus(str(cfg['password']))}"
        f"@{cfg['host']}:{cfg['port']}/{cfg['database']}?charset={cfg['charset']}"
    )
