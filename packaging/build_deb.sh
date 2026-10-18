#!/usr/bin/env bash
# 构建 Debian/Ubuntu 的架构无关 .deb 包。
# 产物：dist/gpu-fan-controller_<version>_all.deb
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VERSION="$(cd "${PROJECT}" && python3 -c 'import gpu_fan_controller; print(gpu_fan_controller.__version__)')"
BUILD="${PROJECT}/build/deb"
ROOT="${BUILD}/gpu-fan-controller_${VERSION}_all"
OUT="${PROJECT}/dist"

if ! command -v dpkg-deb >/dev/null 2>&1; then
  echo "缺少 dpkg-deb（请安装 dpkg-dev）" >&2
  exit 1
fi

rm -rf "${BUILD}"
mkdir -p "${ROOT}/DEBIAN" \
  "${ROOT}/usr/bin" \
  "${ROOT}/usr/lib/gpu-fan-controller" \
  "${ROOT}/usr/share/applications" \
  "${ROOT}/usr/share/icons/hicolor/256x256/apps" \
  "${ROOT}/usr/share/doc/gpu-fan-controller" \
  "${ROOT}/usr/share/gpu-fan-controller" \
  "${ROOT}/usr/lib/udev/rules.d" \
  "${ROOT}/usr/lib/systemd/system" \
  "${ROOT}/etc/gpu-fan-controller" \
  "${OUT}"

# Python 包（私有目录，避免与 pip/dist-packages 冲突）
cp -a "${PROJECT}/gpu_fan_controller" "${ROOT}/usr/lib/gpu-fan-controller/"
find "${ROOT}/usr/lib/gpu-fan-controller" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "${ROOT}/usr/lib/gpu-fan-controller" -name '*.pyc' -delete

# 默认配置
cp "${PROJECT}/config.example.json" "${ROOT}/usr/share/gpu-fan-controller/config.example.json"
cp "${PROJECT}/config.example.json" "${ROOT}/etc/gpu-fan-controller/config.json"

# 启动器
cat > "${ROOT}/usr/bin/gpu-fan-controller" <<'EOF'
#!/bin/sh
export PYTHONPATH="/usr/lib/gpu-fan-controller${PYTHONPATH:+:$PYTHONPATH}"
exec /usr/bin/python3 -m gpu_fan_controller "$@"
EOF
cat > "${ROOT}/usr/bin/gpu-fan-controller-desktop" <<'EOF'
#!/bin/sh
export PYTHONPATH="/usr/lib/gpu-fan-controller${PYTHONPATH:+:$PYTHONPATH}"
exec /usr/bin/python3 -m gpu_fan_controller.desktop "$@"
EOF
chmod 0755 "${ROOT}/usr/bin/gpu-fan-controller" "${ROOT}/usr/bin/gpu-fan-controller-desktop"

# 桌面入口与图标
cat > "${ROOT}/usr/share/applications/gpu-fan-controller.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Version=1.0
Name=Tesla T10 风扇控制器
Name[en]=Tesla T10 Fan Controller
Comment=Tesla T10 GPU 温度驱动的 IPMI 风扇控制
Comment[en]=Tesla T10 GPU temperature driven IPMI fan control
Exec=gpu-fan-controller-desktop
Icon=gpu-fan-controller
Terminal=false
Categories=System;Monitor;
StartupNotify=true
EOF
cp "${PROJECT}/gpu_fan_controller/assets/icon.png" \
  "${ROOT}/usr/share/icons/hicolor/256x256/apps/gpu-fan-controller.png"

# udev / systemd
cp "${PROJECT}/deploy/99-ipmi.rules" "${ROOT}/usr/lib/udev/rules.d/99-ipmi.rules"
cat > "${ROOT}/usr/lib/systemd/system/gpu-fan-controller.service" <<'EOF'
[Unit]
Description=GPU fan controller (headless web mode)
After=network.target

[Service]
Type=simple
User=gpu-fan
SupplementaryGroups=ipmi
WorkingDirectory=/var/lib/gpu-fan-controller
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/usr/bin/gpu-fan-controller --config /etc/gpu-fan-controller/config.json
Restart=always
RestartSec=2
KillSignal=SIGTERM
TimeoutStopSec=60

NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=read-only
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
ReadWritePaths=/etc/gpu-fan-controller
StateDirectory=gpu-fan-controller

[Install]
WantedBy=multi-user.target
EOF

# 文档
cp "${PROJECT}/README.md" "${PROJECT}/CHANGELOG.md" "${PROJECT}/THIRD_PARTY_NOTICES.md" \
  "${ROOT}/usr/share/doc/gpu-fan-controller/"
cat > "${ROOT}/usr/share/doc/gpu-fan-controller/copyright" <<'EOF'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: gpu-fan-controller
Source: https://github.com/OWNER/tesla-t10-fan-controller

Files: *
Copyright: 2026 The GPU Fan Controller Authors
License: MIT
 This package is licensed under the MIT License. The full text is
 available in the upstream LICENSE file and on Debian/Ubuntu systems
 at /usr/share/common-licenses/MIT.
EOF

# Debian 控制信息
cat > "${ROOT}/DEBIAN/control" <<EOF
Package: gpu-fan-controller
Version: ${VERSION}
Architecture: all
Maintainer: local build <root@localhost>
Depends: python3 (>= 3.10), python3-tk
Recommends: ipmitool, python3-pyghmi
Section: utils
Priority: optional
Description: Tesla T10 GPU temperature driven IPMI fan controller
 Controls ASRock Rack EPYCD8 fan channels from NVIDIA GPU temperatures.
 Includes a native Tk desktop application, fan-channel enumeration and
 editable GPU-to-fan mappings. The local web/headless mode is also included.
EOF
echo "/etc/gpu-fan-controller/config.json" > "${ROOT}/DEBIAN/conffiles"
INSTALLED_SIZE=$(du -sk "${ROOT}" | awk '{print $1}')
sed -i "/^Version:/a Installed-Size: ${INSTALLED_SIZE}" "${ROOT}/DEBIAN/control"

cat > "${ROOT}/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e

getent group ipmi >/dev/null 2>&1 || groupadd --system ipmi
if ! getent passwd gpu-fan >/dev/null 2>&1; then
  useradd --system --no-create-home --home-dir /nonexistent \
    --shell /usr/sbin/nologin --groups ipmi gpu-fan
fi
usermod -aG ipmi gpu-fan >/dev/null 2>&1 || true
if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ]; then
  usermod -aG ipmi "${SUDO_USER}" >/dev/null 2>&1 || true
  echo "已将 ${SUDO_USER} 加入 ipmi 组，请重新登录后使用桌面程序。"
fi

install -d -o root -g gpu-fan -m 0770 /etc/gpu-fan-controller
if [ -f /etc/gpu-fan-controller/config.json ]; then
  chown root:gpu-fan /etc/gpu-fan-controller/config.json
  chmod 0660 /etc/gpu-fan-controller/config.json
fi
install -d -o gpu-fan -g gpu-fan -m 0750 /var/lib/gpu-fan-controller

# 旧版手动安装的规则把 SUBSYSTEM 写成了 char，导致设备仍为 root:root
if [ -f /etc/udev/rules.d/99-ipmi.rules ] && \
   grep -q 'SUBSYSTEM=="char"' /etc/udev/rules.d/99-ipmi.rules 2>/dev/null; then
  sed -i 's/SUBSYSTEM=="char"/SUBSYSTEM=="ipmi"/' /etc/udev/rules.d/99-ipmi.rules || true
  echo "已修正 /etc/udev/rules.d/99-ipmi.rules 的 SUBSYSTEM 匹配"
fi

if command -v udevadm >/dev/null 2>&1; then
  udevadm control --reload-rules || true
  udevadm trigger --name-match=/dev/ipmi0 2>/dev/null || true
  udevadm trigger --subsystem-match=ipmi --action=change 2>/dev/null || true
fi
# 确保当前已存在的设备节点立即生效（不依赖 udev 是否重新处理）
for dev in /dev/ipmi[0-9]*; do
  [ -e "$dev" ] || continue
  chgrp ipmi "$dev" 2>/dev/null || true
  chmod 0660 "$dev" 2>/dev/null || true
done
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi
if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload || true
  systemctl try-restart gpu-fan-controller.service || true
fi

echo "gpu-fan-controller installed."
echo "桌面程序：gpu-fan-controller-desktop（或应用菜单）"
echo "后台服务默认未启用：sudo systemctl enable --now gpu-fan-controller"
echo "桌面用户需要加入 ipmi 组：sudo usermod -aG ipmi \$USER（然后重新登录）"
exit 0
EOF

cat > "${ROOT}/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "deconfigure" ]; then
  if command -v systemctl >/dev/null 2>&1; then
    systemctl stop gpu-fan-controller.service 2>/dev/null || true
    systemctl disable gpu-fan-controller.service 2>/dev/null || true
  fi
fi
exit 0
EOF

cat > "${ROOT}/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload || true
fi
if command -v udevadm >/dev/null 2>&1; then
  udevadm control --reload-rules || true
fi
exit 0
EOF

chmod 0755 "${ROOT}/DEBIAN/postinst" "${ROOT}/DEBIAN/prerm" "${ROOT}/DEBIAN/postrm"

# 统一权限，避免构建机 umask 影响
find "${ROOT}" -type d -exec chmod 0755 {} +
find "${ROOT}" -type f -exec chmod 0644 {} +
chmod 0755 "${ROOT}/usr/bin/gpu-fan-controller" "${ROOT}/usr/bin/gpu-fan-controller-desktop"
chmod 0755 "${ROOT}/DEBIAN/postinst" "${ROOT}/DEBIAN/prerm" "${ROOT}/DEBIAN/postrm"

# md5sums
(
  cd "${ROOT}"
  find . -type f ! -path './DEBIAN/*' -printf '%P\0' \
    | sort -z \
    | xargs -0 md5sum > DEBIAN/md5sums
)

OUTPUT="${OUT}/gpu-fan-controller_${VERSION}_all.deb"
dpkg-deb --build --root-owner-group "${ROOT}" "${OUTPUT}"

echo
echo "已生成：${OUTPUT}"
dpkg-deb --info "${OUTPUT}"
echo
echo "安装（推荐，自动解决 python3-tk 依赖）：sudo apt install ${OUTPUT}"
echo "或：sudo dpkg -i ${OUTPUT} && sudo apt -f install"
echo "卸载：sudo dpkg -r gpu-fan-controller"
