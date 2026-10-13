#!/bin/bash
# 命令行启动 wb-openai 转换器（不带面板）
# 用法: ./start.sh [端口]
set -e
cd "$(dirname "$0")"

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "未找到 Python。请先安装 Python 3.8+：https://www.python.org/downloads/"
  exit 1
fi

PORT="${1:-8787}"
LOG="$HOME/wb-openai.log"
echo "启动中… 端口 $PORT  日志 $LOG"
exec "$PY" bootstrap.py converter.py --port "$PORT" --log "$LOG"
