FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    TZ=Asia/Shanghai

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p logs/INFO logs/ERROR data

# 当前代码是 CLI 常驻进程（schedule 循环），不监听任何 HTTP 端口，故不声明对外端口。
# 需要对外提供触发接口时，应另加一层 Web 服务，不在本项目范围内。
# 入口统一用 -m 指向 xiaoxin.cli.app，根目录不再保留转发壳文件。
CMD ["python", "-m", "xiaoxin.cli.app"]
