# Windows 10/11 使用与验证指南

> 重要：Windows 版本尚未在真实 Windows 10/11 硬件上构建和验证。本指南给出完整步骤和验收清单；在目标机器完成验证前，请把 Windows 支持视为“待验证”。

## 1. 前提

- Windows 10 或 Windows 11，x64
- Python 3.12 x64（<https://www.python.org/downloads/windows/>），安装时勾选 **Add python.exe to PATH**
- NVIDIA 驱动已安装，`nvidia-smi.exe` 可运行
- 主板 BMC 已启用 **IPMI over LAN**，Windows 机器能访问 BMC 的 UDP 623 端口
- BMC 用户名/密码（需要 ADMINISTRATOR 权限才能写风扇）

Windows 没有 `/dev/ipmi*` 本地 IPMI 通道，因此只能走网络 IPMI。

## 2. 安装依赖

```bat
py -3.12 -m pip install --upgrade pip
py -3.12 -m pip install -r requirements-network.txt
```

`requirements-network.txt` 安装 `pyghmi`（纯 Python IPMI 2.0 客户端）及其依赖 `cryptography`、`python-dateutil`。

如果无法安装 pyghmi，也可以准备 `ipmitool.exe`，在「设置」页把「网络后端」改为 `ipmitool` 并填写可执行文件路径。

## 3. 从源码运行

把项目复制到 **ASCII 路径**，例如 `C:\GPU-Fan-Controller`（中文/空格路径容易引发 Tcl/Tk 与 PyInstaller 问题）：

```bat
cd C:\GPU-Fan-Controller
py -3.12 -m gpu_fan_controller.desktop --dry-run
```

首次运行若没有可用配置，会在 `%APPDATA%\GpuFanController\config.json` 创建安全默认配置（`control_enabled=false`）。

也可以显式指定配置：

```bat
py -3.12 -m gpu_fan_controller.desktop --config C:\GPU-Fan-Controller\config.json --dry-run
```

## 4. 配置网络 IPMI

打开「设置」页：

| 字段 | 建议值 |
| --- | --- |
| 传输方式 | `network`（`auto` 在 Windows 也选 network） |
| 网络后端 | `pyghmi`（或 `auto`） |
| BMC 地址 | 例如 `192.168.1.100` |
| BMC 端口 | `623` |
| 用户名 | BMC 管理员账号 |
| 密码 | 对应密码；也可留空并用环境变量 |
| IPMI 接口 | `lanplus` |
| 权限级别 | `ADMINISTRATOR` |
| nvidia-smi 路径 | `nvidia-smi`（PATH 中）或完整路径 |

点击「测试连接」。成功时应显示：

```
连接成功：pyghmi-lanplus · 占空比 0%, 0%, 35%, ... · 读到 7 路转速
```

失败按提示检查：BMC 地址/防火墙、IPMI over LAN 是否启用、账号权限、pyghmi 是否安装。

### 密码与环境变量

- 密码写入配置文件时，POSIX 下保存为 0600；Windows 请自行限制 `%APPDATA%\GpuFanController\config.json` 的 ACL。
- 更安全的做法：设置环境变量（默认名 `IPMI_PASSWORD`），配置里的密码留空。注意从开始菜单/自启动启动时环境变量可能不存在。

## 5. 核对风扇映射

1. 打开「风扇映射」页，点击「刷新通道 / GPU」；
2. 确认每块 GPU 的 PCI 地址和实际风扇通道；
3. 无转速通道（例如 `FRNT_FAN3`）会标记为“无转速反馈”，选择时需要确认；
4. 对单块 GPU 做手动 100% 测试，确认降温的是正确的那块卡；
5. 点击「保存并应用映射」。

## 6. 启用控制

在「设置」页勾选「启用真实风扇控制」，保存并应用。程序会：

1. 用新 IPMI 配置做只读预检；
2. 旧控制器把旧通道交还 BMC 自动；
3. 重建后端与控制器；
4. 写回配置并开始按曲线写入。

关闭窗口时，控制器会把受管通道交还 BMC 自动控制（或按 `restore_auto_on_stop` 设为 100%）。

## 7. 构建 Windows 可执行文件

在 Windows 构建机上（不要在 Linux 上交叉构建）：

```bat
cd C:\GPU-Fan-Controller
packaging\build_windows.bat
```

脚本会：

1. 在 `%LOCALAPPDATA%\gpu-fan-controller-build\.venv` 创建 venv；
2. 安装 `requirements-build.txt`（PyInstaller 6.18.x + pyghmi）；
3. 复制源码到 ASCII 构建目录并运行 `packaging/check_env.py`；
4. 构建 onedir 的 CLI 与 GUI 两个产物；
5. 运行 `--version` 和 GUI `--dry-run --smoke-test`；
6. 生成 `dist\BUILD_INFO.txt`。

产物：

```
%LOCALAPPDATA%\gpu-fan-controller-build\dist\
├── gpu-fan-controller\
│   └── gpu-fan-controller.exe          # 控制台：--version/--check/--probe/网页
├── gpu-fan-controller-gui\
│   └── gpu-fan-controller-gui.exe      # 窗口版
└── BUILD_INFO.txt
```

- 默认 onedir、关闭 UPX：启动快、Tcl/Tk 资源可靠、杀毒误报更少。
- 未签名 exe 可能触发 SmartScreen；这是正常提示，可发布 SHA-256 供校验。
- 配置文件不应放在安装目录或 `_MEIPASS`；程序使用 `%APPDATA%` / `%LOCALAPPDATA%`。

## 8. Windows 验收清单

请在一台真实的 Windows 10/11 + 目标 GPU + 目标 BMC 上逐项执行：

- [ ] `gpu-fan-controller.exe --version` 输出 `2.0.0`
- [ ] `gpu-fan-controller.exe --check --config config.json` 校验通过
- [ ] `pyghmi` 网络连接成功，「测试连接」能读到 8 路占空比和风扇转速
- [ ] GUI 正常打开，四块 GPU 温度/风扇/RPM 与 `nvidia-smi` 一致
- [ ] 曲线拖动后保存；关闭再开，曲线保持
- [ ] 「恢复默认曲线」生效并写回配置
- [ ] 风扇映射页枚举出的通道与实际主板一致
- [ ] 对单块 GPU 手动 100%，对应风扇 RPM 上升且 GPU 温度下降
- [ ] 切换回自动后按曲线运行
- [ ] 关闭窗口后风扇交还 BMC 自动（在 BMC Web 或传感器上确认）
- [ ] 强杀进程后重新启动能重新接管（并记录风扇停留状态）
- [ ] 日志写入 `%LOCALAPPDATA%\GpuFanController\logs\app.log`

### 已知未验证项

- Tesla T10 在 Windows 10/11 的驱动可用性（以 NVIDIA 官方支持为准）
- BMC 对多个 LANPlus 会话的限制（设置页“测试连接”会新建会话）
- Windows 休眠/唤醒、用户注销、远程桌面切换时的行为
- 无管理员权限安装、SmartScreen、企业杀毒软件策略
- 中文用户名/路径下的 PyInstaller/Tcl 行为（构建脚本会要求 ASCII 构建目录）

## 9. 故障排查

| 现象 | 处理 |
| --- | --- |
| `ModuleNotFoundError: tkinter` | 用 python.org 官方安装包重装，勾选 Tcl/Tk |
| `pyghmi` 安装失败 | 升级 pip；确认 Python 3.12 x64；必要时安装 Visual C++ 运行库 |
| 连接超时 | 检查 BMC IP、UDP 623、IPMI over LAN、防火墙 |
| 登录失败 | 检查用户名/密码/权限级别；BMC 可能限制会话数 |
| `nvidia-smi` 找不到 | 安装 NVIDIA 驱动，或在设置页填写完整路径 |
| 程序启动即退出 | 查看 `%LOCALAPPDATA%\GpuFanController\logs\app.log` |
| 提示另一个实例在运行 | 关闭另一个 GUI/服务；不要同时运行两个控制器 |
| SmartScreen 拦截 | 校验 SHA-256/来源；正式发布建议代码签名 |
