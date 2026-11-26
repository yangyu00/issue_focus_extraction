#!/usr/bin/env bash
# 小信工单摘要提取：用 docker run 启动，不依赖 docker compose。
# 详细用法见同目录的《run.sh使用说明.txt》。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

IMAGE="${IMAGE:-xiaoxin_work_order:v1}"
NAME="${NAME:-xiaoxin_work_order_qa}"

# 容器以「宿主机当前用户」的 uid/gid 运行：bind mount 按数字 uid 判权，
# 这样容器写出的 logs/ data/ 文件属主就是宿主机当前用户，不用 sudo 就能随便改/删。
# 可用 RUN_UID / RUN_GID 显式覆盖（例如部署账号与登录账号不同时）。
RUN_UID="${RUN_UID:-$(id -u)}"
RUN_GID="${RUN_GID:-$(id -g)}"

if [ "$RUN_UID" = "0" ]; then
  echo "警告：当前以 root(uid=0) 启动，容器内进程仍是 root，" >&2
  echo "      写出的文件属主会是 root，宿主机清理时可能仍需 sudo。" >&2
  echo "      建议用普通用户执行 ./run.sh，或显式指定 RUN_UID=<uid> RUN_GID=<gid>。" >&2
fi

if [ ! -f .env ]; then
  echo "缺少 .env，请先执行：cp .env.example .env 并按实际环境填写" >&2
  exit 1
fi
if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "镜像 $IMAGE 不存在，请先执行：docker build -t $IMAGE ." >&2
  exit 1
fi

# 非 root 进程必须能写挂载目录；先自检，避免容器起来后写日志/Excel 失败却只记日志。
_check_writable() {
  local d="$1" probe="$1/.write_probe.$$"
  # 实际写一个探针文件（比 test -w 更贴近容器真实行为，也能识别只读挂载 / ACL 拒绝）
  if ! (umask 022; : > "$probe") 2>/dev/null; then
    echo "目录不可写: $d (uid=$RUN_UID)" >&2
    echo "一次性修复：sudo chown -R $RUN_UID:$RUN_GID $ROOT_DIR" >&2
    exit 1
  fi
  rm -f "$probe" 2>/dev/null || true
}
_check_writable "$ROOT_DIR"
mkdir -p logs data
_check_writable "$ROOT_DIR/logs"
_check_writable "$ROOT_DIR/data"

# 旧版 root 容器留下的日志/Excel 属主是 root，非 root 进程打不开追加写。
if [ "$RUN_UID" != "0" ]; then
  stale="$(find logs data -mindepth 1 ! -user "$RUN_UID" -print -quit 2>/dev/null || true)"
  if [ -n "$stale" ]; then
    echo "检测到旧版 root 生成的文件: $stale" >&2
    echo "非 root 容器无法改写它。一次性修复：sudo chown -R $RUN_UID:$RUN_GID logs data" >&2
    exit 1
  fi
fi

# 整个项目挂进 /app：宿主机改 .env / prompts / 代码后，docker restart 即生效，
# 不需要重新 build 镜像。
# 注意：这里刻意不用 --env-file .env —— 它只在「创建容器」时把内容固化进环境变量，
# docker restart 不会重读，且优先级高于 config.py 读 /app/.env，会导致改配置不生效。
# 去掉后由 config.py 在每次进程启动时读挂载进来的 /app/.env（restart = 新进程）。
# 代码、.env、prompts 以宿主机挂载为准；只有 requirements.txt 变更才需要重新 build。
MOUNT=(-v "$ROOT_DIR:/app")
# --user 用数字 uid/gid，容器内没有对应 passwd 条目时把 HOME 指到 /tmp，
# 避免个别库想写 ~/.cache 失败；不影响 config.py 读取 /app/.env。
RUN_AS=(--user "$RUN_UID:$RUN_GID" -e HOME=/tmp)

# 不带参数：启动常驻的每日调度服务（CMD 已在 Dockerfile 中指向 xiaoxin_work_order.cli.app）。
if [ "$#" -eq 0 ]; then
  if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
    echo "容器 $NAME 已存在。如需重建，请先执行：docker rm -f $NAME" >&2
    exit 1
  fi
  exec docker run -dit \
    --name "$NAME" \
    --restart unless-stopped \
    "${MOUNT[@]}" \
    "${RUN_AS[@]}" \
    "$IMAGE"
fi

# 带参数：一次性容器执行指定命令（批量补跑 / QA 生成 / 自检），退出后自动删除。
# 同样挂载整个项目、同样非 root，保证手动任务与常驻服务用的是同一份代码/配置/属主。
exec docker run --rm \
  "${MOUNT[@]}" \
  "${RUN_AS[@]}" \
  "$IMAGE" "$@"
