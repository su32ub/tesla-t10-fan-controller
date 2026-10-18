import csv
import os
import shutil
import subprocess
import threading
from typing import Any

from .config import AppConfig

IS_WINDOWS = os.name == "nt"

PYGHMI_PRIVLEVELS = {
    "CALLBACK": 1,
    "USER": 2,
    "OPERATOR": 3,
    "ADMINISTRATOR": 4,
    "OEM": 5,
}


class HardwareError(RuntimeError):
    pass


def resolve_ipmi_transport(config: AppConfig) -> str:
    """auto：Windows 走网络；Linux 有 /dev/ipmi* 走本地，否则有 BMC 地址走网络。"""
    if config.ipmi_transport in {"local", "network"}:
        return config.ipmi_transport
    if IS_WINDOWS:
        return "network"
    for path in ("/dev/ipmi0", "/dev/ipmi/0", "/dev/ipmidev/0"):
        if os.path.exists(path):
            return "local"
    return "network" if config.ipmi_host else "local"


def resolve_ipmi_password(config: AppConfig) -> str | None:
    if config.ipmi_password:
        return config.ipmi_password
    if config.ipmi_password_env and os.environ.get(config.ipmi_password_env):
        return os.environ[config.ipmi_password_env]
    if os.environ.get("IPMI_PASSWORD"):
        return os.environ["IPMI_PASSWORD"]
    return None


class NvidiaSmi:
    def __init__(self, timeout: float, executable: str = "nvidia-smi"):
        self.timeout = timeout
        resolved = shutil.which(executable)
        if resolved is None and IS_WINDOWS and not executable.lower().endswith(".exe"):
            resolved = shutil.which(executable + ".exe")
        self.executable = resolved or executable

    def read_gpus(self) -> dict[int, dict[str, Any]]:
        command = [
            self.executable,
            "--query-gpu=index,name,pci.bus_id,temperature.gpu",
            "--format=csv,noheader,nounits",
        ]
        output = _run(command, self.timeout)
        readings: dict[int, dict[str, Any]] = {}
        for row in csv.reader(output.splitlines()):
            if len(row) != 4:
                continue
            gpu_id = int(row[0].strip())
            temp_text = row[3].strip()
            readings[gpu_id] = {
                "name": row[1].strip(),
                "pci_bus_id": row[2].strip(),
                "temperature_c": float(temp_text) if temp_text not in {"N/A", "[N/A]", ""} else None,
            }
        if not readings:
            raise HardwareError("nvidia-smi 没有返回 GPU 温度")
        return readings


class _IpmiBackendBase:
    """IPMI 后端公共逻辑：8 字节占空比、回读校验、通道枚举、串行化访问。"""

    transport_label = "unknown"

    def __init__(self, config: AppConfig):
        self.config = config
        self.managed_channels = {item.fan_channel for item in config.mappings}
        self._lock = threading.RLock()
        self._config_error: str | None = None

    # ---------------------------------------------------------------- 子类实现

    def _read_register(self) -> list[int]:
        raise NotImplementedError

    def _write_register(self, values: list[int]) -> None:
        raise NotImplementedError

    def _read_fan_details(self) -> dict[str, dict[str, Any]]:
        """返回 {传感器名: {"rpm": float|None, "status": str|None}}。"""
        raise NotImplementedError

    def _read_rpms(self) -> dict[str, float | None]:
        return {
            name: info.get("rpm") for name, info in self._read_fan_details().items()
        }

    # ---------------------------------------------------------------- 公共接口

    def _checked_duties(self) -> list[int]:
        values = self._read_register()
        if len(values) != 8 or any(not 0 <= value <= 100 for value in values):
            raise HardwareError(f"EPYCD8 占空比响应异常：{values!r}")
        return values

    def read_duties(self) -> list[int]:
        with self._lock:
            return self._checked_duties()

    def read_rpms(self) -> dict[str, float | None]:
        with self._lock:
            return self._read_rpms()

    def enumerate_fans(self) -> dict[str, Any]:
        """枚举 8 个受控通道及读数证据。

        present: True=有正转速；False=明确未检测到；None=无法判断。
        has_tach: False 表示传感器存在但无转速读数（例如 FRNT_FAN3）。
        duty_mode: "auto"（寄存器 0）或 "manual"。
        """
        with self._lock:
            duties = self._checked_duties()
            rpm_error = None
            try:
                details = self._read_fan_details()
            except HardwareError as exc:
                details = {}
                rpm_error = str(exc)
            managed_by: dict[int, list[int]] = {}
            for item in self.config.mappings:
                managed_by.setdefault(item.fan_channel, []).append(item.gpu_id)
            channels = []
            for channel, name in enumerate(self.config.ipmi_channel_names):
                info = details.get(name) if name else None
                rpm = info.get("rpm") if info else None
                status = info.get("status") if info else None
                if name is None:
                    present: bool | None = False
                    has_tach: bool | None = None
                    reason = "通道未定义"
                elif rpm_error:
                    present = None
                    has_tach = None
                    reason = "转速读取失败：" + rpm_error
                elif info is None:
                    present = None
                    has_tach = None
                    reason = "传感器未出现在 IPMI 输出中"
                elif rpm is not None and rpm > 0:
                    present = True
                    has_tach = True
                    reason = "有转速"
                elif rpm == 0:
                    present = True
                    has_tach = True
                    reason = "转速为 0（可能停转或未接）"
                elif status in {"na", "unavailable", "ns"}:
                    present = True
                    has_tach = False
                    reason = "传感器存在但无转速读数（通常无测速线）"
                else:
                    present = None
                    has_tach = None
                    reason = "读数不可用"
                channels.append(
                    {
                        "channel": channel,
                        "name": name,
                        "defined": name is not None,
                        "available": name is not None,
                        "role": (
                            "cpu"
                            if name and name.upper().startswith("CPU")
                            else ("fan" if name else "none")
                        ),
                        "rpm": rpm,
                        "rpm_status": status,
                        "duty": duties[channel],
                        "duty_mode": "auto" if duties[channel] == 0 else "manual",
                        "present": present,
                        "has_tach": has_tach,
                        "presence_reason": reason,
                        "managed_by": managed_by.get(channel, []),
                    }
                )
            return {"channels": channels, "rpm_error": rpm_error}

    def write_duties(self, changes: dict[int, int]) -> list[int]:
        with self._lock:
            if not changes:
                return self._checked_duties()
            if not set(changes).issubset(self.managed_channels):
                raise HardwareError("尝试修改未接管的 IPMI 风扇通道")
            if any(not 0 <= value <= 100 for value in changes.values()):
                raise HardwareError("IPMI 占空比必须在 0–100 之间")
            updated = self._checked_duties()
            for channel, duty in changes.items():
                updated[channel] = duty
            self._write_register(updated)
            confirmed = self._checked_duties()
            for channel, duty in changes.items():
                if confirmed[channel] != duty:
                    raise HardwareError(
                        f"通道 {channel} 写入后回读为 {confirmed[channel]}，期望 {duty}"
                    )
            return confirmed

    def release_control(self, channels: set[int]) -> list[int]:
        """把指定通道写回 0x00，交还 BMC 自动控制。"""
        with self._lock:
            if not channels.issubset(self.managed_channels):
                raise HardwareError("尝试释放未接管的 IPMI 风扇通道")
            updated = self._checked_duties()
            for channel in channels:
                updated[channel] = 0
            self._write_register(updated)
            confirmed = self._checked_duties()
            for channel in channels:
                if confirmed[channel] != 0:
                    raise HardwareError(
                        f"通道 {channel} 交还自动控制后回读为 {confirmed[channel]}，期望 0"
                    )
            return confirmed


class AsrockRackIpmi(_IpmiBackendBase):
    """通过 ipmitool 访问 EPYCD8：本地 /dev/ipmi* 或网络 lanplus。"""

    def __init__(self, config: AppConfig, transport: str | None = None):
        super().__init__(config)
        self.transport = transport or resolve_ipmi_transport(config)
        self.transport_label = (
            f"ipmitool-{self.transport}" if self.transport == "local" else "ipmitool-lanplus"
        )
        self.executable = _resolve_executable(config.ipmitool_path)
        self._password: str | None = None
        self._env = dict(os.environ)
        self._network_args: list[str] = []
        if self.transport == "network":
            if not config.ipmi_host.strip():
                self._config_error = "网络 IPMI 未配置 BMC 地址（ipmi_host）"
            elif not config.ipmi_username.strip():
                self._config_error = "网络 IPMI 未配置用户名（ipmi_username）"
            else:
                self._password = resolve_ipmi_password(config)
                if not self._password:
                    self._config_error = (
                        "网络 IPMI 未配置密码（ipmi_password 或环境变量 "
                        f"{config.ipmi_password_env or 'IPMI_PASSWORD'}）"
                    )
                else:
                    self._env["IPMI_PASSWORD"] = self._password
            self._network_args = [
                "-I",
                config.ipmi_interface,
                "-H",
                config.ipmi_host,
                "-p",
                str(config.ipmi_port),
                "-U",
                config.ipmi_username,
                "-L",
                config.ipmi_privilege,
                "-E",
                "-N",
                "3",
                "-R",
                "2",
            ]

    def _ipmi(self, *args: str) -> str:
        if self._config_error:
            raise HardwareError(self._config_error)
        command = [self.executable, *self._network_args, *args]
        return _run(
            command,
            self.config.command_timeout,
            env=self._env if self.transport == "network" else None,
        )

    def _read_register(self) -> list[int]:
        output = self._ipmi("raw", "0x3a", "0x02")
        try:
            return [int(token, 16) for token in output.split()]
        except ValueError as exc:
            raise HardwareError(f"无法解析 IPMI 占空比：{output!r}") from exc

    def _write_register(self, values: list[int]) -> None:
        args = ["raw", "0x3a", "0x01", *(f"0x{value:02x}" for value in values)]
        self._ipmi(*args)

    def _read_fan_details(self) -> dict[str, dict[str, Any]]:
        output = self._ipmi("sensor")
        watched = {name for name in self.config.ipmi_channel_names if name}
        details: dict[str, dict[str, Any]] = {}
        for line in output.splitlines():
            parts = [part.strip() for part in line.split("|")]
            if len(parts) < 2 or parts[0] not in watched:
                continue
            status = parts[3].lower() if len(parts) > 3 and parts[3] else None
            try:
                rpm = float(parts[1])
            except ValueError:
                rpm = None
            details[parts[0]] = {"rpm": rpm, "status": status}
        return details


class PyghmiIpmi(_IpmiBackendBase):
    """通过 pyghmi 走 IPMI 2.0 网络协议，Windows/Linux 都不需要外部 ipmitool。"""

    transport_label = "pyghmi-lanplus"

    def __init__(self, config: AppConfig):
        super().__init__(config)
        try:
            from pyghmi.ipmi import command as pyghmi_command
        except ImportError as exc:  # pragma: no cover - 取决于运行环境
            raise HardwareError(
                "未安装 pyghmi。请执行 pip install pyghmi，或改用 ipmitool 后端。"
            ) from exc
        self._pyghmi_command = pyghmi_command
        self._ipmi = None
        self._password = resolve_ipmi_password(config)
        if not config.ipmi_host.strip():
            self._config_error = "网络 IPMI 未配置 BMC 地址（ipmi_host）"
        elif not config.ipmi_username.strip():
            self._config_error = "网络 IPMI 未配置用户名（ipmi_username）"
        elif not self._password:
            self._config_error = (
                "网络 IPMI 未配置密码（ipmi_password 或环境变量 "
                f"{config.ipmi_password_env or 'IPMI_PASSWORD'}）"
            )
        raw_priv = config.ipmi_privilege.strip()
        if raw_priv.isdigit():
            self._privlevel = int(raw_priv)
        else:
            self._privlevel = PYGHMI_PRIVLEVELS.get(raw_priv.upper(), raw_priv)

    def _command(self):
        if self._config_error:
            raise HardwareError(self._config_error)
        if self._ipmi is None:
            try:
                self._ipmi = self._pyghmi_command.Command(
                    bmc=self.config.ipmi_host,
                    userid=self.config.ipmi_username,
                    password=self._password,
                    port=self.config.ipmi_port,
                    privlevel=self._privlevel,
                )
            except Exception as exc:
                raise HardwareError(f"pyghmi 连接失败：{exc}") from exc
        return self._ipmi

    def _raw(self, netfn: int, command: int, data=()) -> list[int]:
        ipmi = self._command()
        try:
            response = ipmi.raw_command(netfn=netfn, command=command, data=tuple(data))
        except Exception as exc:
            self._ipmi = None
            raise HardwareError(f"pyghmi 通信失败：{exc}") from exc
        if not isinstance(response, dict):
            raise HardwareError(f"pyghmi 响应异常：{response!r}")
        if "error" in response:
            raise HardwareError(f"pyghmi 返回错误：{response['error']}")
        return [int(value) for value in response.get("data", [])]

    def _read_register(self) -> list[int]:
        return self._raw(0x3A, 0x02)

    def _write_register(self, values: list[int]) -> None:
        self._raw(0x3A, 0x01, values)

    def _read_fan_details(self) -> dict[str, dict[str, Any]]:
        watched = [name for name in self.config.ipmi_channel_names if name]
        details: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        for name in watched:
            try:
                reading = self._command().get_sensor_reading(name)
            except Exception as exc:
                details[name] = {"rpm": None, "status": "error", "error": str(exc)}
                errors.append(f"{name}: {exc}")
                continue
            value = getattr(reading, "value", None)
            unavailable = bool(getattr(reading, "unavailable", 0))
            if value is None or unavailable:
                details[name] = {"rpm": None, "status": "unavailable"}
            else:
                try:
                    details[name] = {"rpm": float(value), "status": "ok"}
                except (TypeError, ValueError):
                    details[name] = {"rpm": None, "status": "unavailable"}
        if watched and errors and len(errors) == len(watched):
            self._ipmi = None
            raise HardwareError("pyghmi 读取风扇转速失败：" + "；".join(errors[:2]))
        return details


class DryRunIpmi(_IpmiBackendBase):
    """模拟 EPYCD8，用于演练模式和测试。"""

    transport_label = "dry-run"

    def __init__(self, config: AppConfig):
        super().__init__(config)
        self.duties = [35, 0, 35, 35, 45, 45, 45, 45]

    def _read_register(self) -> list[int]:
        return list(self.duties)

    def _write_register(self, values: list[int]) -> None:
        self.duties = list(values)

    def _read_fan_details(self) -> dict[str, dict[str, Any]]:
        details: dict[str, dict[str, Any]] = {}
        for channel, name in enumerate(self.config.ipmi_channel_names):
            if not name:
                continue
            if name == "FRNT_FAN3":
                details[name] = {"rpm": None, "status": "na"}
                continue
            duty = self.duties[channel] or 35
            details[name] = {
                "rpm": float(round((1800 + channel * 125) * duty / 100)),
                "status": "ok",
            }
        return details


def create_ipmi_backend(config: AppConfig, dry_run: bool = False) -> _IpmiBackendBase:
    if dry_run:
        return DryRunIpmi(config)
    transport = resolve_ipmi_transport(config)
    if transport == "local":
        return AsrockRackIpmi(config, transport="local")
    if config.ipmi_network_backend in {"auto", "pyghmi"}:
        try:
            return PyghmiIpmi(config)
        except HardwareError:
            if config.ipmi_network_backend == "pyghmi":
                raise
    return AsrockRackIpmi(config, transport="network")


def _resolve_executable(path: str) -> str:
    if os.path.dirname(path):
        return path
    resolved = shutil.which(path)
    if resolved is None and IS_WINDOWS and not path.lower().endswith(".exe"):
        resolved = shutil.which(path + ".exe")
    return resolved or path


def _run(command: list[str], timeout: float, env: dict[str, str] | None = None) -> str:
    kwargs: dict[str, Any] = {}
    if IS_WINDOWS:
        # pythonw 运行时避免每次调用弹出一个控制台窗口
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
            **kwargs,
        )
    except FileNotFoundError as exc:
        raise HardwareError(f"找不到命令：{command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise HardwareError(f"命令超时：{command[0]}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"退出码 {result.returncode}"
        if "Could not open device" in detail:
            detail += (
                "（IPMI 设备权限不足：当前用户需要 root 或 ipmi 组；"
                "见 README 的 udev 配置）"
            )
        raise HardwareError(detail)
    return result.stdout
