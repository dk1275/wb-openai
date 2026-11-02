#!/bin/bash
# wb-openai 控制面板一键启动（macOS）
# 双击本文件即可：自动准备依赖 -> 起面板 -> 打开浏览器
cd "$(dirname "$0")"

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done

if [ -z "$PY" ]; then
  echo "未找到 Python。请先安装：https://www.python.org/downloads/"
  echo "（或通过 Homebrew: brew install python）"
  read -r -p "按回车退出…"
  exit 1
fi

exec "$PY" bootstrap.py wb_panel.py "$@"
