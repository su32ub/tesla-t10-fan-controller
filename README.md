# Tesla T10 风扇控制器 V2.0

[![tests](https://github.com/su32ub/tesla-t10-fan-controller/actions/workflows/tests.yml/badge.svg)](https://github.com/su32ub/tesla-t10-fan-controller/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)


针对 **4 张 NVIDIA Tesla T10** 的 GPU 温度控制：读取每张 T10 的核心温度，通过 IPMI 调节 ASRock Rack EPYCD8 主板对应风扇的跨平台应用。

V2.0 提供**原生桌面窗口**（Tkinter）、**风扇通道枚举与可编辑映射**、以及 **Windows 10/11 支持**；V1 的本地网页/无头模式继续保留。

- 后端：Python 3.10+，核心功能仅用标准库
- 桌面界面：Tkinter/ttk（Windows 官方 Python 自带；Linux 需 `python3-tk`）
- IPMI：Linux 本地 `ipmitool`（`/dev/ipmi*`）；网络 IPMI 支持 `pyghmi`（Windows 推荐）或 `ipmitool -I lanplus`
- 默认只读监控（`control_enabled=false`），确认映射后再显式开启真实控制

## 功能

| 功能 | 说明 |
| --- | --- |
| 原生桌面窗口 | 监控、风扇映射、设置、日志四个页签，不再依赖浏览器 |
| 自动曲线 | 按 GPU 温度线性插值控制对应风扇 |
| 曲线编辑 | 拖动 30/40/50/60/70/80°C 六个控制点，松开自动保存；一键恢复默认曲线 |
| 手动控制 | 每块 GPU 可切自动/手动，滑块调节占空比（下限可配置） |
| 风扇通道枚举 | 读取 8 路 IPMI 通道的传感器状态、RPM、占空比，标出“有风扇 / 无转速反馈 / 读数未知” |
| 映射编辑 | 为每块检测到的 GPU 选择散热风扇通道；保存后控制器自动重启并接管 |
| 热保护 | ≥ `thermal_override_temp` 强制 100%，降到 `thermal_release_temp` 以下才解除 |
| 失效保护 | 温度过期、GPU 消失、转速缺失时降级；无转速通道可按映射显式豁免 |
| PCI 地址绑定 | 按 `pci_bus_id` 绑定 GPU，nvidia-smi 索引变化不会控制错风扇 |
| 写入校验 | 写 IPMI 后回读确认，不一致会报错并展示 |
| 退出恢复 | 正常停止/重载时把受管通道交还 BMC 自动控制（可配置为 100%） |
| 单实例保护 | 桌面程序、网页/headless 实例互斥；检测 systemd 服务并拒绝双写 |
| 跨平台 | Linux 本地 IPMI；Windows 10/11 网络 IPMI（pyghmi 或 ipmitool.exe） |

## 目录结构

```
tesla-t10-fan-controller/
├── config.example.json            # 配置模板（复制为 config.json 后使用）
├── config.json                    # 本地配置（.gitignore 忽略，可能含 IPMI 密码）
├── gpu-fan-controller.service     # 通用 systemd 无头/网页服务模板
├── gpu_fan_controller/
│   ├── __main__.py                # CLI + 网页/headless 入口
│   ├── desktop/                   # V2 桌面应用
│   │   ├── __main__.py            # 桌面入口（单实例/服务冲突检查）
│   │   ├── app.py                 # 主窗口、页签、设置
│   │   ├── host.py                # 控制器/配置生命周期
│   │   ├── widgets.py             # GPU 卡片、可拖拽曲线
│   │   └── theme.py               # 浅色主题
│   ├── config.py                  # 配置/曲线/映射校验、原子写入
│   ├── hardware.py                # nvidia-smi、ipmitool/pyghmi、通道枚举
│   ├── controller.py              # 采样、热保护、写风扇、失保
│   ├── instance_lock.py           # 跨平台单实例锁
│   ├── web.py + static/           # V1 网页界面（保留）
│   └── assets/icon.png            # 应用图标
├── deploy/
│   ├── install.sh                 # systemd 无头服务安装
│   ├── install-desktop.sh         # Linux 应用菜单入口
│   ├── run-desktop.sh             # 从源码启动桌面程序
│   ├── 99-ipmi.rules              # /dev/ipmi* 权限
│   └── identify_fan.py            # 风扇通道/GPU 对应关系测试
├── packaging/                     # .deb / PyInstaller 构建脚本
├── tests/                         # unittest（89 个用例）
├── docs/windows.md                # Windows 使用与验收清单
├── .github/workflows/tests.yml    # GitHub Actions：Linux/Windows 测试 + .deb 构建
├── LICENSE                        # MIT
├── CONTRIBUTING.md
├── SECURITY.md
└── pyproject.toml                 # 版本 2.0.0、console/GUI 入口
```

## Linux 快速开始

> Debian/Ubuntu 用户也可以直接构建并安装 `.deb`（见下文“构建发布”），它会自动处理依赖、应用菜单、udev 和 systemd 单元。

### 0. 获取代码

```bash
git clone https://github.com/<你的用户名>/tesla-t10-fan-controller.git
cd tesla-t10-fan-controller
cp config.example.json config.json
```

`config.json` 是机器相关配置，已被 `.gitignore` 忽略；不要提交 IPMI 密码。

### 1. 依赖

```bash
sudo apt install python3-tk python3-venv python3-pip
python3 -m venv .venv
. .venv/bin/activate
# 可选：网络 IPMI（本地 /dev/ipmi* 模式不需要）
pip install -r requirements-network.txt
```

桌面启动脚本会自动优先使用项目内的 `.venv/bin/python`。Tkinter 来自系统 `python3-tk`，venv 会直接使用它。

### 2. 先在演练模式确认界面

```bash
python3 -m gpu_fan_controller.desktop --config config.json --dry-run
```

演练模式不会写主板，可完整测试监控、曲线拖动、手动滑块和映射页。

### 3. IPMI 权限（真实控制）

`/dev/ipmi0` 默认只有 root 可读写：

```bash
sudo groupadd --system ipmi 2>/dev/null || true
sudo usermod -aG ipmi "$USER"
sudo install -m 0644 deploy/99-ipmi.rules /etc/udev/rules.d/99-ipmi.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --name-match=/dev/ipmi0
# 重新登录或 newgrp ipmi 后生效
```

桌面程序启动时会在错误横幅提示当前会话是否有 `/dev/ipmi0` 读写权限。

### 4. 停止旧的后台服务

V2 桌面程序会检测 `gpu-fan-controller.service` 或 8080 端口上的网页实例；两者同时写同一组 IPMI 占空比字节会互相覆盖，因此桌面程序默认拒绝启动。

```bash
sudo systemctl stop gpu-fan-controller
# 如果以后只用桌面程序：
sudo systemctl disable gpu-fan-controller
```

### 5. 安装应用菜单入口

```bash
./deploy/install-desktop.sh
```

之后可在应用菜单搜索“Tesla T10 风扇控制器”，或直接：

```bash
./deploy/run-desktop.sh
```

### 6. 启用真实控制

在桌面程序「设置」页勾选“启用真实风扇控制”，保存并应用。建议先：

1. 在「风扇映射」页刷新通道，核对每块 GPU 对应的风扇；
2. 对单块 GPU 切手动 100%，确认温度下降的是正确的那块卡；
3. 再开启全局控制。

## Windows 10/11

详细步骤见 [docs/windows.md](docs/windows.md)。要点：

1. 安装 **Python 3.12 x64**（python.org，安装时勾选 Add to PATH；Tkinter 随官方安装包提供）。
2. 安装网络 IPMI 依赖（推荐）：
   ```bat
   py -3.12 -m pip install -r requirements-network.txt
   ```
3. 从源码运行：
   ```bat
   py -3.12 -m gpu_fan_controller.desktop
   ```
4. 在「设置」页：
   - 传输方式选 `network`（Windows 上 `auto` 也会选 network）；
   - 网络后端选 `pyghmi`（或 `auto`）；
   - 填写 BMC 地址、用户名、密码；
   - 保存前点「测试连接」确认能读到 IPMI 占空比和转速。
5. `nvidia-smi.exe` 需在 PATH 中（安装 NVIDIA 驱动后通常已在 `C:\Windows\System32`）。

Windows 上**没有** `/dev/ipmi*` 本地通道，必须使用网络 IPMI。若没有 pyghmi，也可以提供 `ipmitool.exe` 并把路径填到设置页。

### 构建 Windows 可执行文件

在 Windows 构建机上：

```bat
packaging\build_windows.bat
```

输出在 `%LOCALAPPDATA%\gpu-fan-controller-build\dist\`，包含：

- `gpu-fan-controller\gpu-fan-controller.exe`（控制台：`--version` / `--check` / `--probe` / 网页模式）
- `gpu-fan-controller-gui\gpu-fan-controller-gui.exe`（窗口版桌面程序）

> 当前项目是在 Linux 上开发的，**Windows 产物尚未在真实 Windows 10/11 上构建和验证**。请按 [docs/windows.md](docs/windows.md) 的验证清单在目标机器上测试 Tkinter 显示、nvidia-smi、pyghmi 连接、控制写入和退出恢复。

## 桌面界面

### 监控

- 页签顶部是自动温度曲线：蓝色曲线、热保护区域、GPU 当前位置（橙色编号圆点）
- 拖动蓝色控制点（30–80°C）调整曲线，松开自动保存到 `config.json`
- 点击「恢复默认曲线」还原出厂曲线
- 下方每块 GPU 一张卡片：温度/RPM、目标/实际占空比、自动/手动、滑块、告警

### 风扇映射

- 点击「刷新通道 / GPU」读取实际硬件：
  - 通道号、传感器名、RPM、当前占空比（寄存器 0 显示为“BMC 自动”）
  - 状态：有风扇 / 无转速反馈 / 读数未知 / 未检测到
- 为每块 GPU 选择散热风扇；同一通道不能被两块 GPU 选中
- 选择无转速通道（例如 `FRNT_FAN3`）会提示，并自动把该映射标记为“不校验 RPM”
- 选择 `CPU1_FAN1` 等 CPU 散热通道时会额外确认
- 点击「保存并应用映射」：先只读预检新 IPMI 配置 → 旧控制器交还 BMC 自动 → 重建后端和控制器 → 写回配置

### 设置

- IPMI：传输方式（auto/local/network）、网络后端（auto/pyghmi/ipmitool）、BMC 地址/端口/用户/密码/密码环境变量、接口、权限级别、ipmitool 与 nvidia-smi 路径
- 控制：启用真实控制、采样间隔、数据过期、热保护触发/解除、手动下限/默认值
- 「测试连接」只读验证候选配置；「保存并应用」会重启控制器

### 日志

桌面程序日志同时写入：

- Linux：`~/.local/state/gpu-fan-controller/app.log`
- Windows：`%LOCALAPPDATA%\GpuFanController\logs\app.log`

并实时显示在「日志」页签。

## 配置参考

V1 配置可直接加载；缺失的新字段使用默认值。首次从 V1 保存时会生成 `config.json.v1.bak`。

| 键 | 默认 | 说明 |
| --- | --- | --- |
| `config_version` | `2` | 配置版本 |
| `host` / `port` | `127.0.0.1:8080` | V1 网页/headless 监听地址 |
| `poll_interval` | `2.0` | 采样间隔（秒），≥1 |
| `stale_after` | `10.0` | 温度过期阈值（秒） |
| `thermal_override_temp` | `85` | 热保护触发温度 |
| `thermal_release_temp` | `78` | 热保护解除温度 |
| `min_manual_duty` | `20` | 手动/拖拽下限 |
| `default_manual_duty` | `60` | 切手动时的初始值 |
| `command_timeout` | `8.0` | 单次 nvidia-smi/ipmitool 超时 |
| `control_enabled` | `false` | 是否真实写风扇 |
| `restore_auto_on_stop` | `true` | 正常停止交还 BMC 自动；`false` 则设为 100% |
| `log_level` | `INFO` | 日志级别 |
| `ipmitool_path` | `ipmitool` | 本地/网络 ipmitool 路径 |
| `ipmi_transport` | `auto` | `auto/local/network`；Windows auto→network |
| `ipmi_network_backend` | `auto` | `auto/pyghmi/ipmitool` |
| `ipmi_host` | `""` | BMC 地址（网络模式必填） |
| `ipmi_port` | `623` | RMCP 端口 |
| `ipmi_username` | `""` | BMC 用户名 |
| `ipmi_password` | `""` | BMC 密码（POSIX 保存时 chmod 600） |
| `ipmi_password_env` | `IPMI_PASSWORD` | 密码环境变量名 |
| `ipmi_interface` | `lanplus` | ipmitool 接口 |
| `ipmi_privilege` | `ADMINISTRATOR` | IPMI 权限级别 |
| `nvidia_smi_path` | `nvidia-smi` | nvidia-smi 路径 |
| `ipmi_channel_names` | 8 项 | 通道 0–7 的传感器名；`null` 表示未定义 |
| `auto_curve` | 见文件 | `[温度, 占空比]` 数组，温度严格递增 |
| `mappings` | 4 项 | `gpu_id`、`pci_bus_id`、`fan_name`、`fan_channel`、`expect_rpm` |

### 映射规则

- 每块 GPU 对应一个风扇通道，1:1，通道不可重复。
- 运行时优先按 `pci_bus_id` 绑定；nvidia-smi 索引变化会在界面显示映射警告。
- `fan_name` 必须与 `ipmi_channel_names[fan_channel]` 一致。
- `expect_rpm=false` 表示该通道无转速反馈（如 `FRNT_FAN3`），控制器不会因缺少 RPM 而暂停全部写入。

### 占空比 0 的含义

ASRock 的占空比寄存器中 `0x00` 表示**交还 BMC 自动控制**，不是 0%。桌面界面会显示“BMC 自动”。

## 安全策略

| 条件 | 行为 |
| --- | --- |
| 温度数据过期 / 从未读成功 | 该 GPU 对应风扇 100% |
| GPU 温度 ≥ `thermal_override_temp` | 100%，直到 ≤ `thermal_release_temp` |
| 受管通道无 RPM 且 `expect_rpm=true` | 该路 100%，并暂停所有写入 |
| `expect_rpm=false` 的无转速通道 | 不触发缺失 RPM 停写；界面提前警告 |
| IPMI 读取失败 | 本周期不写，界面显示错误 |
| 写入回读不一致 | 记录控制错误 |
| 正常停止（SIGTERM/关窗） | 受管通道交还 BMC 自动（或按 `restore_auto_on_stop` 设为 100%） |
| 应用映射/设置 | 旧通道强制交还 BMC 自动，再按新配置接管 |
| 进程被 SIGKILL/断电 | 无法清理；重启控制器后重新接管 |

### 单实例与双写保护

- 桌面程序和 `python -m gpu_fan_controller` 都通过本地 socket 锁（默认 127.0.0.1:47654）互斥。
- 桌面程序额外检测 systemd 服务与网页 API；检测到就拒绝启动并提示先停止服务。
- `--allow-concurrent` 仅用于演练/调试，正式环境不要使用。

## V1 网页/无头模式（保留）

```bash
sudo ./deploy/install.sh          # 安装 systemd 服务
systemctl status gpu-fan-controller
```

服务使用与 V1 相同的 `ExecStart`，因此旧的安装方式仍然可用。网页界面地址为配置中的 `host:port`（默认 <http://127.0.0.1:8080>）。

网页/headless 模式不会加载 Tkinter，适合无图形界面的服务器。

## 命令行

```bash
# 版本
python3 -m gpu_fan_controller --version

# 校验配置
python3 -m gpu_fan_controller --check --config config.json

# 只读探测 GPU/IPMI（不写风扇）
python3 -m gpu_fan_controller --probe --config config.json

# 桌面程序（演练）
python3 -m gpu_fan_controller.desktop --config config.json --dry-run

# 打包产物冒烟
python3 -m gpu_fan_controller.desktop --config config.json --dry-run --smoke-test --allow-concurrent

# 风扇通道与 GPU 对应关系测试（自动恢复原占空比）
sudo python3 deploy/identify_fan.py --channel 3 --duty 100 --seconds 150

# 网页/headless 模式
python3 -m gpu_fan_controller --config config.json
```

配置路径优先级：`--config` > `GPU_FAN_CONTROLLER_CONFIG` 环境变量 > 冻结程序同目录 > 项目根目录 > 当前目录 > 用户配置目录（Linux `~/.config/gpu-fan-controller/`，Windows `%APPDATA%\GpuFanController\`）。

## HTTP API（网页模式）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/state` | 完整状态快照 |
| GET | `/api/health` | `{"ok": true}` |
| POST | `/api/gpus/<id>/control` | `{"mode":"auto"}` 或 `{"mode":"manual","duty_percent":55}` |
| PUT | `/api/curve` | `{"curve":[[30,35],[40,38],[60,50],[80,85]]}` |
| POST | `/api/curve/reset` | 恢复默认曲线 |

POST/PUT 要求 `Content-Type: application/json`。服务默认只监听 127.0.0.1。

## 测试

```bash
python3 -m unittest discover -v
```

当前 89 个用例，覆盖曲线、热保护、失效降级、无转速映射、IPMI 本地/网络/ pyghmi 适配、通道枚举、配置迁移/备份/并发写入、单实例锁、控制器重载、HTTP API 与版本一致性。

## 构建发布

### Debian / Ubuntu（推荐）

```bash
cd tesla-t10-fan-controller
./packaging/build_deb.sh
sudo apt install ./dist/gpu-fan-controller_2.0.0_all.deb
```

> 如果之前用 `deploy/install.sh` 安装过系统级单元，该文件位于 `/etc/systemd/system/gpu-fan-controller.service`，会覆盖 `.deb` 提供的同名单元。迁移到 `.deb` 前建议先清理：
>
> ```bash
> sudo systemctl disable --now gpu-fan-controller
> sudo rm -f /etc/systemd/system/gpu-fan-controller.service
> sudo systemctl daemon-reload
> ```

生成的是架构无关（`Architecture: all`）包，依赖系统 `python3-tk`，推荐安装 `ipmitool` 和 `python3-pyghmi`。安装内容：

| 路径 | 说明 |
| --- | --- |
| `/usr/bin/gpu-fan-controller` | CLI / 网页/headless 模式 |
| `/usr/bin/gpu-fan-controller-desktop` | 桌面程序 |
| `/usr/share/applications/gpu-fan-controller.desktop` | 应用菜单入口 |
| `/usr/lib/gpu-fan-controller/` | Python 包（私有目录） |
| `/usr/lib/systemd/system/gpu-fan-controller.service` | 无头服务（**默认不启用**） |
| `/usr/lib/udev/rules.d/99-ipmi.rules` | `/dev/ipmi*` 权限 |
| `/etc/gpu-fan-controller/config.json` | 服务用配置（conffile，`control_enabled=false`） |

`postinst` 会创建 `ipmi` 组和 `gpu-fan` 服务用户；如果通过 `sudo dpkg -i` 安装，还会把 `SUDO_USER` 加入 `ipmi` 组（需重新登录）。

```bash
# 桌面程序（配置在 ~/.config/gpu-fan-controller/config.json）
gpu-fan-controller-desktop

# 可选：启用后台/网页服务（配置在 /etc/gpu-fan-controller/config.json）
sudo systemctl enable --now gpu-fan-controller
```

> 桌面程序和服务使用不同的配置文件，并且不能同时运行（单实例锁）。从项目迁移配置时，可把现有 `config.json` 复制到 `~/.config/gpu-fan-controller/config.json`。
>
> 该 `.deb` 是本地/私有构建：项目尚未声明开源许可证，`copyright` 中标记为 private，请勿在未确认许可的情况下对外分发。

### 通用 Linux 二进制（PyInstaller）

```bash
./packaging/build_linux.sh
```

脚本会：

1. 检查 `python3-tk`、创建 venv、安装 `requirements-build.txt`（PyInstaller 6.18.x + pyghmi）；
2. 把源码复制到 ASCII 构建目录（默认 `~/build/gpu-fan-controller-2.0.0`）；
3. 运行 `packaging/check_env.py`（Tk 可创建、PyInstaller 版本、单元测试）；
4. 构建 onedir CLI 与 GUI 两个产物；
5. 用 `xvfb-run` 对 GUI 做 `--dry-run --smoke-test` 冒烟；
6. 生成 `dist/BUILD_INFO.txt`。

### Windows

```bat
packaging\build_windows.bat
```

同样构建 CLI + GUI，输出在 `%LOCALAPPDATA%\gpu-fan-controller-build\dist\`。使用 onedir、关闭 UPX，便于排查且减少杀毒误报。

> 构建机必须是 ASCII 路径；脚本会检查构建目录。Windows 产物需要在真实 Windows 10/11 上完成 [docs/windows.md](docs/windows.md) 的验证清单，不能仅凭 Linux 上的单元测试宣称可用。

## 故障排查

| 现象 | 排查 |
| --- | --- |
| 桌面程序提示“检测到另一个实例/systemd 服务” | `sudo systemctl stop gpu-fan-controller`，或关闭另一个桌面窗口 |
| `ModuleNotFoundError: tkinter` | `sudo apt install python3-tk` |
| `Could not open device at /dev/ipmi0` | 先 `ls -l /dev/ipmi0`：应为 `root ipmi 0660`。若为 `root root 0600`，执行 `sudo chgrp ipmi /dev/ipmi0 && sudo chmod 0660 /dev/ipmi0`；并确认 udev 规则是 `KERNEL=="ipmi*", GROUP="ipmi", MODE="0660"`（`/dev/ipmi0` 的 SUBSYSTEM 是 `ipmi`，不是 `char`） |
| Windows 连接失败 | 设置页填 BMC 地址/用户/密码；先点「测试连接」；确认 pyghmi 已安装或 ipmitool.exe 路径正确 |
| 映射页读不到通道 | 检查 IPMI 权限/网络；确认 `ipmitool sensor` 或 pyghmi 能读传感器 |
| 风扇转速不变 | 确认映射正确、通道有转速；无转速通道无法校验 |
| 想恢复主板自动控制 | 停止程序（正常退出会交还 BMC 自动），或在 BMC Web 切回 Auto |
| GUI 无日志/闪退 | 查看 `~/.local/state/gpu-fan-controller/app.log` 或 `%LOCALAPPDATA%\GpuFanController\logs\app.log` |

## 已知限制

- Windows 版本未在真实 Windows 10/11 硬件上验证；Tesla T10 的 Windows 驱动支持请以 NVIDIA 官方为准。
- 网络 IPMI 需要 BMC 允许 LANPlus；密码保存在配置文件中（POSIX chmod 600，Windows 请自行保护文件 ACL）。
- ASRock 的 `raw 0x3a 0x01` 一次写全部 8 路；程序只修改映射中的通道，其余写回当前值。
- 1:1 映射，暂不支持多块 GPU 共享同一风扇通道。
- 桌面程序与服务不能同时运行；切换时请先停止服务。
- 进程被强杀或断电时无法执行交还 BMC；风扇会停留在最后占空比。
- Linux 二进制在 Ubuntu 24.04 上构建时受 glibc 版本限制；如需兼容旧发行版，请在较旧构建机或 CI 上构建。

## 发布到 GitHub

本仓库已经准备好 `.gitignore`、MIT LICENSE、GitHub Actions 工作流和贡献/安全说明。首次上传：

```bash
# 如果系统还没有 git
sudo apt install git

cd /path/to/gpu-fan-controller

git init -b main
git add .
git status                 # 确认没有 config.json / 日志 / dist / build
git commit -m "GPU Fan Controller 2.0.0"

# 在 GitHub 新建空仓库（不要勾选初始化 README），然后：
git remote add origin git@github.com:<你的用户名>/tesla-t10-fan-controller.git
git push -u origin main

# 打版本标签
git tag -a v2.0.0 -m "GPU Fan Controller 2.0.0"
git push origin v2.0.0
```

建议在仓库设置中：

- 把 `.deb` 上传到 **Releases**，而不是提交进仓库；
- 将默认分支设为 `main`；
- Topics 可填：`gpu`、`fan-control`、`ipmi`、`nvidia`、`tesla`、`asrock`、`tkinter`、`linux`、`windows`；
- 创建仓库后，把 README 顶部 CI 徽章注释里的 `OWNER` 改成你的 GitHub 用户名；
- 如需自动发布 `.deb`，可在 Releases 页面手动附加 `dist/gpu-fan-controller_2.0.0_all.deb`，或后续增加 tag 触发的 release workflow。

不要提交：

- `config.json`（机器相关，可能含 BMC 密码）；
- `build/`、`dist/`、`__pycache__/`、`.venv/`；
- 日志和真实 IPMI 地址/凭据。

## 贡献与安全

- 开发与提交说明：[CONTRIBUTING.md](CONTRIBUTING.md)
- 安全策略与凭据处理：[SECURITY.md](SECURITY.md)
- 第三方组件许可：[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

## 版本

- V1.0：网页/无头控制器、自动/手动、曲线编辑、热保护。
- V2.0（当前）：原生桌面窗口、风扇通道枚举与映射编辑、IPMI 本地/网络抽象、pyghmi 支持、单实例保护、Debian `.deb` 与 Windows 构建脚本。

## License

[MIT](LICENSE) © 2026 The GPU Fan Controller Authors
