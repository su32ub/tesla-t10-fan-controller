#!/bin/sh
# 从源码目录启动桌面程序（优先使用项目内 .venv）。
DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$DIR" || exit 1
if [ -x "$DIR/.venv/bin/python" ]; then
  PY="$DIR/.venv/bin/python"
else
  PY=python3
fi
if [ ! -f "$DIR/config.json" ]; then
  cp "$DIR/config.example.json" "$DIR/config.json"
fi
exec "$PY" -m gpu_fan_controller.desktop --config "$DIR/config.json" "$@"
