#!/usr/bin/env bash
# Aurora 停止服务脚本：./stop.sh
set -euo pipefail
cd "$(dirname "$0")"

PID_FILE=".aurora-web.pid"

# 发送 TERM 后最多等 5 秒，仍未退出则强制结束
terminate() {
    kill "$1" 2>/dev/null || true
    for _ in $(seq 1 10); do
        if ! kill -0 "$1" 2>/dev/null; then
            return 0
        fi
        sleep 0.5
    done
    kill -9 "$1" 2>/dev/null || true
}

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    terminate "$(cat "$PID_FILE")"
    rm -f "$PID_FILE"
    echo "Aurora 工作台已停止。"
    exit 0
fi

# PID 文件丢失时兜底：按启动命令精确匹配（-x 避免误杀包含该字符串的其他 shell）
PIDS=$(pgrep -xf "python3 -m aurora_web.*" || true)
if [[ -n "$PIDS" ]]; then
    for PID in $PIDS; do
        terminate "$PID"
    done
    rm -f "$PID_FILE"
    echo "Aurora 工作台已停止（PID: $(echo $PIDS | tr '\n' ' ')）。"
else
    rm -f "$PID_FILE"
    echo "服务未在运行。"
fi
