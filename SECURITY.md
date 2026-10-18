# 安全说明

## 不要提交凭据

`config.json` 可能包含 `ipmi_password`。本仓库的 `.gitignore` 已排除该文件。推荐使用环境变量：

```bash
export IPMI_PASSWORD='...'
```

并在配置中把 `ipmi_password` 留空、`ipmi_password_env` 设为 `IPMI_PASSWORD`。

## 本地 Web 接口

网页/headless 模式默认只监听 `127.0.0.1`，且没有登录认证。不要直接暴露到公网；如需远程访问，请使用 SSH 隧道或带认证的反向代理，并限制来源 IP。

## IPMI 设备权限

- Linux 本地模式需要访问 `/dev/ipmi*`；仅授予受信任用户 `ipmi` 组权限。
- Windows 网络模式使用 BMC 账号密码；请保护 `%APPDATA%\GpuFanController\config.json` 的 ACL。
- 程序不会把密码写入命令行参数：ipmitool 通过 `IPMI_PASSWORD` 环境变量读取，pyghmi 通过会话参数传递。

## 风扇控制风险

本程序会真实改写主板风扇占空比。错误的 GPU↔风扇映射可能导致 GPU 过热。请：

- 先在 `--dry-run` 下验证；
- 用单块 GPU 手动 100% 测试确认对应关系；
- 保持热保护和失效保护逻辑不被绕过。

## 报告漏洞

请通过 GitHub 仓库的 **Security → Report a vulnerability** 私密报告，不要在公开 Issue 中粘贴 BMC 密码、内网地址或硬件序列号。
