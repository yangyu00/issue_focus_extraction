#!/usr/bin/env bash
# 小信智能体摘要提取：用 docker run 启动，不依赖 docker compose。
# 详细用法见同目录的《run.sh使用说明.txt》。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

IMAGE="${IMAGE:-xiaoxin:v1}"
NAME="${NAME:-xiaoxin_qa}"

if [ ! -f .env ]; then
  echo "缺少 .env，请先执行：cp .env.example .env 并按实际环境填写" >&2
  exit 1
fi
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "镜像 $IMAGE 不存在，请先执行：docker build -t $IMAGE ." >&2
  exit 1
fi
mkdir -p logs data

# 不带参数：启动常驻的每日调度服务（CMD 已在 Dockerfile 中指向 xiaoxin.cli.app）。
if [ "$#" -eq 0 ]; then
  if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
    echo "容器 $NAME 已存在。如需重建，请先执行：docker rm -f $NAME" >&2
    exit 1
  fi
  exec docker run -dit \
    --name "$NAME" \
    --restart unless-stopped \
    --env-file .env \
    -v "$ROOT_DIR/logs:/app/logs" \
    -v "$ROOT_DIR/data:/app/data" \
    "$IMAGE"
fi

# 带参数：一次性容器执行指定命令（批量补跑 / QA 生成 / 自检），退出后自动删除。
exec docker run --rm \
  --env-file .env \
  -v "$ROOT_DIR/logs:/app/logs" \
  -v "$ROOT_DIR/data:/app/data" \
  "$IMAGE" "$@"
