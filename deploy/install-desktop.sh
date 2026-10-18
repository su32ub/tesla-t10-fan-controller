#!/usr/bin/env bash
# 安装 Linux 桌面启动器（应用菜单入口），不修改 systemd 服务。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$(cd "${SCRIPT_DIR}/.." && pwd)"
if [[ -x "${WORKSPACE}/.venv/bin/python" ]]; then
  PYTHON="${WORKSPACE}/.venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi
APPS_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/icons/hicolor/256x256/apps"

if ! "${PYTHON}" -c 'import tkinter' >/dev/null 2>&1; then
  echo "缺少 tkinter。请先执行：" >&2
  echo "  sudo apt install python3-tk" >&2
  exit 1
fi

mkdir -p "${APPS_DIR}" "${ICON_DIR}"
if [[ ! -f "${WORKSPACE}/config.json" ]]; then
  cp "${WORKSPACE}/config.example.json" "${WORKSPACE}/config.json"
  echo "已从 config.example.json 创建 ${WORKSPACE}/config.json"
fi
cp "${WORKSPACE}/gpu_fan_controller/assets/icon.png" "${ICON_DIR}/gpu-fan-controller.png"

cat > "${APPS_DIR}/gpu-fan-controller.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Tesla T10 风扇控制器
Comment=Tesla T10 GPU 温度驱动的 IPMI 风扇控制
Exec=${PYTHON} -m gpu_fan_controller.desktop --config ${WORKSPACE}/config.json
Path=${WORKSPACE}
Icon=gpu-fan-controller
Terminal=false
Categories=System;Monitor;
StartupNotify=true
EOF
chmod +x "${APPS_DIR}/gpu-fan-controller.desktop"
chmod +x "${WORKSPACE}/deploy/run-desktop.sh"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "${APPS_DIR}" >/dev/null 2>&1 || true
fi

echo "桌面入口已安装：${APPS_DIR}/gpu-fan-controller.desktop"
echo "图标：${ICON_DIR}/gpu-fan-controller.png"
echo
echo "注意：如果 systemd 服务还在运行，桌面程序会拒绝启动以避免双重控制。请先执行："
echo "  sudo systemctl stop gpu-fan-controller"
echo "  # 如不再需要后台服务： sudo systemctl disable gpu-fan-controller"
