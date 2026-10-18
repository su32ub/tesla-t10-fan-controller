#!/usr/bin/env bash
# 安装 GPU 风扇控制器为 systemd 服务，并配置 /dev/ipmi* 访问权限。
# 用法：sudo ./deploy/install.sh
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "请使用 sudo 运行：sudo $0" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUN_USER="${SUDO_USER:-$(stat -c '%U' "${WORKSPACE}")}"
RUN_GROUP="$(id -gn "${RUN_USER}")"
PYTHON="$(command -v python3)"
UNIT_SRC="${WORKSPACE}/gpu-fan-controller.service"
UNIT_DST="/etc/systemd/system/gpu-fan-controller.service"

if [[ ! -f "${WORKSPACE}/config.json" ]]; then
  cp "${WORKSPACE}/config.example.json" "${WORKSPACE}/config.json"
  echo "已从 config.example.json 创建 ${WORKSPACE}/config.json"
  echo "请先按硬件修改映射/IPMI 配置，再从 control_enabled=false 开始验证。"
fi

if [[ ! -f "${WORKSPACE}/config.json" ]]; then
  echo "找不到 ${WORKSPACE}/config.json，请先检查目录结构" >&2
  exit 1
fi

echo "[1/4] 创建 ipmi 组并把 ${RUN_USER} 加入该组"
getent group ipmi >/dev/null || groupadd --system ipmi
usermod -aG ipmi "${RUN_USER}"

echo "[2/4] 安装 udev 规则并修复当前设备权限"
install -m 0644 "${SCRIPT_DIR}/99-ipmi.rules" /etc/udev/rules.d/99-ipmi.rules
udevadm control --reload-rules
udevadm trigger --name-match=/dev/ipmi0 2>/dev/null || true
for dev in /dev/ipmi[0-9]*; do
  [[ -e "${dev}" ]] || continue
  chgrp ipmi "${dev}"
  chmod 0660 "${dev}"
done

echo "[3/4] 安装 systemd 服务（运行用户：${RUN_USER}）"
sed \
  -e "s|^User=.*|User=${RUN_USER}|" \
  -e "s|^Group=.*|Group=${RUN_GROUP}|" \
  -e "s|^WorkingDirectory=.*|WorkingDirectory=${WORKSPACE}|" \
  -e "s|^ReadWritePaths=.*|ReadWritePaths=${WORKSPACE}|" \
  -e "s|^ExecStart=.*|ExecStart=${PYTHON} -m gpu_fan_controller --config ${WORKSPACE}/config.json|" \
  "${UNIT_SRC}" > "${UNIT_DST}"
chmod 0644 "${UNIT_DST}"
systemctl daemon-reload
systemctl enable gpu-fan-controller.service
systemctl restart gpu-fan-controller.service

echo "[4/4] 服务状态"
systemctl --no-pager --full --lines=15 status gpu-fan-controller.service || true

cat <<EOF

安装完成。接下来：
  1. 当前配置 control_enabled=false，服务只监控不写风扇。
  2. 重新登录（或执行 newgrp ipmi）后，用以下命令核对硬件与映射：
       cd "${WORKSPACE}"
       python3 -m gpu_fan_controller --config config.json --probe
  3. 确认映射无误后，把 config.json 里的 "control_enabled" 改为 true，
     然后重启服务：
       sudo systemctl restart gpu-fan-controller
  4. 面板地址见 config.json 的 host/port（默认 http://127.0.0.1:8080）。
EOF
