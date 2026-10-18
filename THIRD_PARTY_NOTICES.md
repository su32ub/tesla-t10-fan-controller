# 第三方组件说明

本项目采用 **MIT License**，详见 [LICENSE](LICENSE)。

运行时可能使用以下第三方组件：

| 组件 | 用途 | 许可证 |
| --- | --- | --- |
| pyghmi | 网络 IPMI（Windows 推荐、Linux 可选） | Apache License 2.0 |
| ipmitool | Linux 本地 IPMI / 可选的网络 IPMI 后端 | BSD 3-Clause |
| Tcl/Tk | Tkinter 桌面界面运行时 | BSD-style |
| Python | 运行时 | PSF License |

构建工具（不随应用代码分发）：

| 工具 | 用途 | 许可证 |
| --- | --- | --- |
| PyInstaller | 生成 onedir 发布包 | GPLv2 with bootloader exception |
| Pillow | 生成应用图标（仅开发期） | HPND |

如果分发包含 `ipmitool.exe` 的 Windows 包，请自行确认所使用构建的来源、SHA-256 与 BSD-3-Clause 许可声明；本项目不自动下载或捆绑该二进制。
