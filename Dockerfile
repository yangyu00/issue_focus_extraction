FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    TZ=Asia/Shanghai

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 用 run.sh 启动时，整个项目目录会以 bind mount 挂到 /app，覆盖这里 COPY 的内容：
# 改 .env / prompts/*.txt / *.py 后 docker restart 即生效，无需重新 build。
# 但 pip 依赖装在这一层，改 requirements.txt 必须重新 build 镜像。
COPY . .

# 镜像内目录先归 uid 1000（无 bind mount 直接跑镜像时也能写日志/Excel）。
RUN mkdir -p logs/INFO logs/ERROR data \
 && chown -R 1000:1000 logs data

# 镜像默认以非 root(uid=1000) 运行：容器里不再有 root 权限。
# 用 run.sh 启动时会被 --user <宿主机uid>:<gid> 覆盖，让 bind mount 写出的文件
# 属主与宿主机当前用户一致（数字 uid 无需 passwd 条目，不依赖 useradd）。
USER 1000:1000

# 当前代码是 CLI 常驻进程（schedule 循环），不监听任何 HTTP 端口，故不声明对外端口。
# 需要对外提供触发接口时，应另加一层 Web 服务，不在本项目范围内。
# 入口统一用 -m 指向 xiaoxin_work_order.cli.app，根目录不再保留转发壳文件。
CMD ["python", "-m", "xiaoxin_work_order.cli.app"]
