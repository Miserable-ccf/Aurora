#!/usr/bin/env bash
# Aurora 一键启动脚本
# 用法：./start.sh [start|stop|restart|status|logs|crawl]
#   start    启动网页工作台（默认，后台运行）
#   stop     停止服务
#   restart  重启服务（改代码后用）
#   status   查看运行状态
#   logs     跟踪日志
#   crawl    立即抓一轮新公告（职位表自动解析入库）
# 启动前会先抓取一轮最新公告；设置 AURORA_SKIP_CRAWL=1 可跳过抓取。
set -euo pipefail
cd "$(dirname "$0")"

DB="aurora.db"
HOST="127.0.0.1"
PORT="${AURORA_PORT:-18100}"
PID_FILE=".aurora-web.pid"
LOG_FILE="aurora-web.log"

ensure_deps() {
    if ! python3 -c "import fastapi, uvicorn" 2>/dev/null; then
        echo "缺少网页依赖，请先执行：python3 -m pip install -e '.[web]'"
        exit 1
    fi
}

is_running() {
    [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

start() {
    ensure_deps
    if is_running; then
        echo "服务已在运行（PID $(cat "$PID_FILE")）：http://$HOST:$PORT/"
        return 0
    fi
    if [[ "${AURORA_SKIP_CRAWL:-0}" != "1" ]]; then
        echo "启动前抓取最新公告（职位表自动解析入库）..."
        if python3 -m aurora_monitor --db "$DB" run-once; then
            echo "抓取完成。"
        else
            echo "抓取失败（可能无网络），继续启动服务。"
        fi
    fi
    nohup python3 -m aurora_web --db "$DB" --host "$HOST" --port "$PORT" >>"$LOG_FILE" 2>&1 &
    echo $! >"$PID_FILE"
    sleep 2
    if is_running; then
        echo "Aurora 工作台已启动：http://$HOST:$PORT/  （PID $(cat "$PID_FILE")）"
        echo "停止：./start.sh stop    日志：./start.sh logs"
    else
        echo "启动失败，最近日志："
        tail -5 "$LOG_FILE"
        rm -f "$PID_FILE"
        exit 1
    fi
}

stop() {
    if is_running; then
        kill "$(cat "$PID_FILE")" 2>/dev/null || true
        rm -f "$PID_FILE"
        echo "已停止。"
    else
        rm -f "$PID_FILE"
        echo "服务未在运行。"
    fi
}

case "${1:-start}" in
    start) start ;;
    stop) stop ;;
    restart) stop; start ;;
    status)
        if is_running; then echo "运行中（PID $(cat "$PID_FILE")）：http://$HOST:$PORT/"; else echo "未运行"; fi
        ;;
    logs) tail -f "$LOG_FILE" ;;
    crawl) python3 -m aurora_monitor --db "$DB" run-once ;;
    *) echo "用法：$0 [start|stop|restart|status|logs|crawl]"; exit 1 ;;
esac
