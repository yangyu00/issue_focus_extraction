import os

# HOST = "0.0.0.0"
# PORT = 8628  # 模型端口配置
# TG_URL = "/api/sms/detection"  # 路由配置

PROJECT_PATH = os.path.dirname(os.path.abspath(__file__))  # 获取当前脚本文件所在的目录路径
BASE_LOG_PATH = os.path.join(PROJECT_PATH, "logs")  # 日志根目录
INFO_LOG_PATH = os.path.join(BASE_LOG_PATH, "INFO")  # INFO日志目录
ERROR_LOG_PATH = os.path.join(BASE_LOG_PATH, "ERROR")  # ERROR日志目录
INFO_LOG_NAME = "xiaoxin-info"
ERROR_LOG_NAME = "xiaoxin-error"

GENERATE_QA_INFO_LOG_NAME = "generate_qa_knowledge-info"
GENERATE_QA_ERROR_LOG_NAME = "generate_qa_knowledge-error"

for dir__ in [BASE_LOG_PATH, INFO_LOG_PATH, ERROR_LOG_PATH]:
    if not os.path.exists(dir__):
        # exist_ok=True参数表示如果目录已经存在，则不会引发异常
        os.makedirs(dir__, exist_ok=True)
