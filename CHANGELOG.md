# 更新记录

## 2.0.0

### 新增

- 原生桌面应用（Tkinter/ttk）：监控、风扇映射、设置、日志四个页签
- 可拖拽温度曲线（30/40/50/60/70/80°C 控制点）与一键恢复默认曲线
- 风扇通道枚举：显示 RPM、占空比、传感器状态、有风扇/无转速反馈/读数未知
- GPU ↔ 风扇通道映射编辑，支持无转速通道（`expect_rpm=false`）
- IPMI 传输抽象：
  - Linux 本地 `ipmitool`（`/dev/ipmi*`）
  - 网络 `pyghmi`（Windows 推荐，可选依赖）
  - 网络 `ipmitool -I lanplus`（密码通过 `IPMI_PASSWORD` 环境变量传递）
- Windows 10/11 源码运行与 PyInstaller 构建脚本
- 单实例 socket 锁 + systemd/网页实例检测，防止双控制器同时写风扇
- 配置 V1→V2 迁移、`config.json.v1.bak` 备份、统一加锁原子写入
- `pyproject.toml`、console/GUI 入口、Linux 桌面启动器、应用图标
- Debian/Ubuntu `.deb` 打包：架构无关包、conffile、udev 规则、systemd 单元、服务用户、应用菜单入口
- 构建环境检查、CLI/GUI 分离的 PyInstaller onedir 规格、冻结产物冒烟测试

### 修复

- 无转速通道不再导致全局写风扇停止（按映射的 `expect_rpm` 区分）
- 停止控制线程与失败保护写入之间的竞争：新增写锁并在写入前检查停止标志
- 配置/映射重载时强制把旧通道交还 BMC 自动，即使 `restore_auto_on_stop=false`
- 映射/settings 应用先做只读预检，失败时保留旧控制器
- 曲线保存与设置保存共享同一把配置锁，使用唯一临时文件、fsync 与 Windows 替换重试
- 布尔配置严格解析，避免 `bool("false") == True`
- IPMI 网络模式密码不进入命令行参数
- Windows 子进程使用 `CREATE_NO_WINDOW`，避免控制台窗口闪烁

### 保留

- V1 网页/无头模式、HTTP API、systemd 服务与安装脚本
- 按 PCI 地址绑定 GPU、热保护、温度过期/无 RPM 失效保护、写后回读校验

## 1.0.0

- 初版：Python 标准库 + 本地网页界面
- 自动曲线、手动控制、热保护、写校验、systemd/udev 部署
