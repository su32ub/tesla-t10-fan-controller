#!/usr/bin/env bash
# 在 ASCII 路径下构建 Linux onedir 发布包。
# 需要：python3、python3-venv、python3-tk、pip、xvfb-run（无图形会话时）。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$(cd "${SCRIPT_DIR}/.." && pwd)"
STAGE="${BUILD_DIR:-${HOME}/build/gpu-fan-controller-2.0.0}"

if [[ "${STAGE}" =~ [^[:ascii:]] ]]; then
  echo "构建目录包含非 ASCII 字符：${STAGE}" >&2
  exit 1
fi

echo "== 检查系统依赖 =="
python3 -c 'import tkinter' 2>/dev/null || {
  echo "缺少 tkinter，请执行： sudo apt install python3-tk python3-venv python3-pip xvfb" >&2
  exit 1
}
python3 -m venv "${STAGE}/.venv" 2>/dev/null || {
  echo "无法创建 venv，请安装 python3-venv（以及 python3-pip）" >&2
  exit 1
}
# shellcheck disable=SC1091
source "${STAGE}/.venv/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r "${PROJECT}/requirements-build.txt"

echo "== 准备 ASCII 构建目录 =="
rm -rf "${STAGE}/src" "${STAGE}/dist" "${STAGE}/work"
mkdir -p "${STAGE}/src"
cp -a "${PROJECT}/gpu_fan_controller" "${PROJECT}/packaging" \
  "${PROJECT}/tests" "${PROJECT}/config.example.json" \
  "${PROJECT}/pyproject.toml" "${PROJECT}/README.md" "${STAGE}/src/"
find "${STAGE}/src" -name '__pycache__' -type d -prune -exec rm -rf {} +

cd "${STAGE}/src"
if [[ -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]]; then
  if command -v xvfb-run >/dev/null 2>&1; then
    RUN=(xvfb-run -a)
  else
    echo "没有图形会话且缺少 xvfb-run，请安装 xvfb 或在图形会话中构建" >&2
    exit 1
  fi
else
  RUN=()
fi

"${RUN[@]}" python packaging/check_env.py

echo "== PyInstaller: CLI =="
python -m PyInstaller --noconfirm --clean \
  --distpath "${STAGE}/dist" --workpath "${STAGE}/work/cli" \
  packaging/gpu-fan-controller-cli.spec

echo "== PyInstaller: GUI =="
python -m PyInstaller --noconfirm --clean \
  --distpath "${STAGE}/dist" --workpath "${STAGE}/work/gui" \
  packaging/gpu-fan-controller-gui.spec

echo "== 冒烟测试 =="
"${STAGE}/dist/gpu-fan-controller/gpu-fan-controller" --version
cp "${STAGE}/src/config.example.json" "${STAGE}/smoke-config.json"
"${RUN[@]}" "${STAGE}/dist/gpu-fan-controller-gui/gpu-fan-controller-gui" \
  --config "${STAGE}/smoke-config.json" --dry-run --smoke-test --allow-concurrent

{
  echo "Python: $(python -VV)"
  echo "PyInstaller: $(python -c 'import PyInstaller; print(PyInstaller.__version__)')"
  echo "Tk: $(python -c 'import tkinter; print(tkinter.TkVersion)')"
  echo "OS: $(uname -a)"
  echo "Built: $(date -Iseconds)"
} > "${STAGE}/dist/BUILD_INFO.txt"

echo "== 完成 =="
echo "输出：${STAGE}/dist/"
