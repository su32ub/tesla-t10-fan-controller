import json
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
CONFIG_VERSION = 2
_CONFIG_LOCK = threading.RLock()
IPMI_TRANSPORTS = ("auto", "local", "network")
IPMI_NETWORK_BACKENDS = ("auto", "pyghmi", "ipmitool")

DEFAULT_AUTO_CURVE = (
    (0.0, 30),
    (60.0, 40),
    (70.0, 55),
    (75.0, 70),
    (80.0, 85),
    (85.0, 100),
    (100.0, 100),
)

DEFAULT_CHANNEL_NAMES = (
    "CPU1_FAN1",
    None,
    "REAR_FAN1",
    "REAR_FAN2",
    "FRNT_FAN1",
    "FRNT_FAN2",
    "FRNT_FAN3",
    "FRNT_FAN4",
)

DEFAULT_MAPPINGS = (
    (0, "00000000:01:00.0", "REAR_FAN2", 3),
    (1, "00000000:41:00.0", "FRNT_FAN4", 7),
    (2, "00000000:81:00.0", "REAR_FAN1", 2),
    (3, "00000000:82:00.0", "FRNT_FAN2", 5),
)


@dataclass(frozen=True)
class GpuMapping:
    gpu_id: int
    pci_bus_id: str
    fan_name: str
    fan_channel: int
    expect_rpm: bool = True


@dataclass(frozen=True)
class AppConfig:
    host: str
    port: int
    poll_interval: float
    stale_after: float
    thermal_override_temp: float
    thermal_release_temp: float
    min_manual_duty: int
    default_manual_duty: int
    command_timeout: float
    control_enabled: bool
    ipmitool_path: str
    ipmi_channel_names: tuple[str | None, ...]
    auto_curve: tuple[tuple[float, int], ...]
    mappings: tuple[GpuMapping, ...]
    log_level: str = "INFO"
    restore_auto_on_stop: bool = True
    config_version: int = CONFIG_VERSION
    ipmi_transport: str = "auto"
    ipmi_network_backend: str = "auto"
    ipmi_host: str = ""
    ipmi_port: int = 623
    ipmi_username: str = ""
    ipmi_password: str = ""
    ipmi_password_env: str = "IPMI_PASSWORD"
    ipmi_interface: str = "lanplus"
    ipmi_privilege: str = "ADMINISTRATOR"
    nvidia_smi_path: str = "nvidia-smi"


def normalize_pci_bus_id(value: str) -> str:
    """把 00000000:01:00.0 / 0000:01:00.0 / 01:00.0 统一成小写四位 domain 形式。"""
    text = str(value).strip().lower()
    parts = text.split(":")
    try:
        if len(parts) == 3:
            return f"{int(parts[0], 16):04x}:{parts[1]}:{parts[2]}"
        if len(parts) == 2:
            return f"0000:{parts[0]}:{parts[1]}"
    except ValueError:
        pass
    return text


def validate_curve(curve) -> tuple[tuple[float, int], ...]:
    """校验并规范化 [温度, 占空比] 曲线。"""
    if not isinstance(curve, (list, tuple)) or len(curve) < 2:
        raise ValueError("自动曲线至少需要两个点")
    result: list[tuple[float, int]] = []
    previous_temp = float("-inf")
    for point in curve:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("自动曲线每个点必须是 [温度, 占空比]")
        raw_temp, raw_duty = point
        if isinstance(raw_temp, bool) or isinstance(raw_duty, bool):
            raise ValueError("自动曲线不能包含布尔值")
        if not isinstance(raw_temp, (int, float)) or not isinstance(raw_duty, (int, float)):
            raise ValueError("自动曲线温度和占空比必须是数字")
        temp = float(raw_temp)
        duty = int(round(raw_duty))
        if not -50 <= temp <= 200:
            raise ValueError("自动曲线温度超出允许范围（-50–200°C）")
        if temp <= previous_temp:
            raise ValueError("自动曲线温度必须严格递增")
        if not 0 <= duty <= 100:
            raise ValueError("自动曲线占空比必须在 0–100 之间")
        result.append((temp, duty))
        previous_temp = temp
    return tuple(result)


def _as_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off", ""}:
            return False
        return default
    return bool(value)


def normalize_mappings(mappings) -> tuple[GpuMapping, ...]:
    """接受 GpuMapping 或字典，返回规范化后的映射元组。"""
    result: list[GpuMapping] = []
    for item in mappings:
        if isinstance(item, GpuMapping):
            result.append(item)
            continue
        if not isinstance(item, dict):
            raise ValueError("映射项必须是对象")
        result.append(
            GpuMapping(
                gpu_id=int(item["gpu_id"]),
                pci_bus_id=str(item["pci_bus_id"]),
                fan_name=str(item["fan_name"]),
                fan_channel=int(item["fan_channel"]),
                expect_rpm=_as_bool(item.get("expect_rpm", True), True),
            )
        )
    return tuple(result)


def validate_mappings(
    mappings, channel_names: tuple[str | None, ...] | list[str | None]
) -> None:
    if not mappings:
        raise ValueError("至少需要一路 GPU 风扇映射")
    gpu_ids = [item.gpu_id for item in mappings]
    channels = [item.fan_channel for item in mappings]
    buses = [normalize_pci_bus_id(item.pci_bus_id) for item in mappings]
    if len(set(gpu_ids)) != len(gpu_ids) or len(set(channels)) != len(channels):
        raise ValueError("GPU 编号和风扇通道都不得重复")
    if len(set(buses)) != len(buses):
        raise ValueError("PCI 地址不得重复")
    for item in mappings:
        if item.gpu_id < 0:
            raise ValueError(f"GPU 编号 {item.gpu_id} 无效")
        if not item.pci_bus_id.strip():
            raise ValueError(f"GPU{item.gpu_id} 缺少 pci_bus_id")
        if not 0 <= item.fan_channel < len(channel_names):
            raise ValueError(f"GPU{item.gpu_id} 的风扇通道超出 0–{len(channel_names) - 1}")
        expected = channel_names[item.fan_channel]
        if expected is None or expected != item.fan_name:
            raise ValueError(f"GPU{item.gpu_id} 的风扇名称与控制通道不一致")


def parse_config(raw: dict) -> AppConfig:
    """从已解析的 JSON 字典构建并校验配置。"""
    mappings = normalize_mappings(raw["mappings"])
    curve = validate_curve(raw["auto_curve"])
    raw_names = raw["ipmi_channel_names"]
    if not isinstance(raw_names, (list, tuple)):
        raise ValueError("ipmi_channel_names 必须是数组")
    channel_names_list: list[str | None] = []
    for item in raw_names:
        if item is None:
            channel_names_list.append(None)
        elif isinstance(item, str):
            text = item.strip()
            channel_names_list.append(text or None)
        else:
            raise ValueError("ipmi_channel_names 的每一项必须是字符串或 null")
    channel_names = tuple(channel_names_list)
    config = AppConfig(
        host=str(raw.get("host", "127.0.0.1")),
        port=int(raw.get("port", 8080)),
        poll_interval=float(raw.get("poll_interval", 2.0)),
        stale_after=float(raw.get("stale_after", 8.0)),
        thermal_override_temp=float(raw.get("thermal_override_temp", 85)),
        thermal_release_temp=float(raw.get("thermal_release_temp", 78)),
        min_manual_duty=int(raw.get("min_manual_duty", 20)),
        default_manual_duty=int(raw.get("default_manual_duty", 60)),
        command_timeout=float(raw.get("command_timeout", 8.0)),
        control_enabled=_as_bool(raw.get("control_enabled", False)),
        ipmitool_path=str(raw.get("ipmitool_path", "ipmitool")),
        ipmi_channel_names=channel_names,
        auto_curve=curve,
        mappings=mappings,
        log_level=str(raw.get("log_level", "INFO")).upper(),
        restore_auto_on_stop=_as_bool(raw.get("restore_auto_on_stop", True), True),
        config_version=int(raw.get("config_version", 1)),
        ipmi_transport=str(raw.get("ipmi_transport", "auto")).lower(),
        ipmi_network_backend=str(raw.get("ipmi_network_backend", "auto")).lower(),
        ipmi_host=str(raw.get("ipmi_host", "")),
        ipmi_port=int(raw.get("ipmi_port", 623)),
        ipmi_username=str(raw.get("ipmi_username", "")),
        ipmi_password=str(raw.get("ipmi_password", "")),
        ipmi_password_env=str(raw.get("ipmi_password_env", "IPMI_PASSWORD")),
        ipmi_interface=str(raw.get("ipmi_interface", "lanplus")),
        ipmi_privilege=str(raw.get("ipmi_privilege", "ADMINISTRATOR")),
        nvidia_smi_path=str(raw.get("nvidia_smi_path", "nvidia-smi")),
    )
    _validate(config)
    return config


def load_config(path: str) -> AppConfig:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return parse_config(raw)


def save_config(path: str, raw: dict) -> None:
    """所有配置写入的唯一入口：加锁、唯一临时文件、fsync、原子替换。

    首次从 V1 升级时保留一份 config.json.v1.bak。
    """
    with _CONFIG_LOCK:
        target = Path(path)
        payload = json.dumps(raw, ensure_ascii=False, indent=2) + "\n"
        if target.exists():
            try:
                old = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                old = None
            if isinstance(old, dict) and "config_version" not in old:
                backup = target.with_name(target.name + ".v1.bak")
                if not backup.exists():
                    try:
                        shutil.copy2(target, backup)
                    except OSError:
                        pass
        temp_path = target.with_name(
            f"{target.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex[:8]}.tmp"
        )
        try:
            with open(temp_path, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                if raw.get("ipmi_password"):
                    os.chmod(temp_path, 0o600)
                elif target.exists():
                    os.chmod(temp_path, target.stat().st_mode)
            except OSError:
                pass
            for attempt in range(5):
                try:
                    os.replace(temp_path, target)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.05)
        finally:
            try:
                temp_path.unlink()
            except OSError:
                pass


def save_auto_curve(path: str, curve) -> None:
    """把曲线写回配置文件，与 mapping/settings 保存共享同一把锁。"""
    validated = validate_curve(curve)
    with _CONFIG_LOCK:
        target = Path(path)
        raw = json.loads(target.read_text(encoding="utf-8"))
        raw["auto_curve"] = [[temp, duty] for temp, duty in validated]
        save_config(path, raw)


def default_raw_config() -> dict:
    """首次运行或 Windows 安装时使用的安全默认配置。"""
    return {
        "config_version": CONFIG_VERSION,
        "host": "127.0.0.1",
        "port": 8080,
        "poll_interval": 2.0,
        "stale_after": 10.0,
        "thermal_override_temp": 85,
        "thermal_release_temp": 78,
        "min_manual_duty": 20,
        "default_manual_duty": 60,
        "command_timeout": 8.0,
        "control_enabled": False,
        "log_level": "INFO",
        "restore_auto_on_stop": True,
        "ipmitool_path": "ipmitool",
        "ipmi_transport": "auto",
        "ipmi_network_backend": "auto",
        "ipmi_host": "",
        "ipmi_port": 623,
        "ipmi_username": "",
        "ipmi_password": "",
        "ipmi_password_env": "IPMI_PASSWORD",
        "ipmi_interface": "lanplus",
        "ipmi_privilege": "ADMINISTRATOR",
        "nvidia_smi_path": "nvidia-smi",
        "ipmi_channel_names": list(DEFAULT_CHANNEL_NAMES),
        "auto_curve": [[temp, duty] for temp, duty in DEFAULT_AUTO_CURVE],
        "mappings": [
            {
                "gpu_id": gpu_id,
                "pci_bus_id": pci,
                "fan_name": fan,
                "fan_channel": channel,
                "expect_rpm": True,
            }
            for gpu_id, pci, fan, channel in DEFAULT_MAPPINGS
        ],
    }


def _validate(config: AppConfig) -> None:
    if config.config_version > CONFIG_VERSION or config.config_version < 1:
        raise ValueError(f"不支持的配置版本：{config.config_version}")
    if not 1 <= config.port <= 65535:
        raise ValueError("port 必须在 1–65535 之间")
    if config.poll_interval < 1:
        raise ValueError("poll_interval 不能小于 1 秒")
    if config.stale_after <= config.poll_interval:
        raise ValueError("stale_after 必须大于 poll_interval")
    if config.command_timeout <= 0:
        raise ValueError("command_timeout 必须大于 0")
    if config.thermal_release_temp >= config.thermal_override_temp:
        raise ValueError("thermal_release_temp 必须低于 thermal_override_temp")
    if not 20 <= config.min_manual_duty <= 100:
        raise ValueError("min_manual_duty 必须在 20–100 之间")
    if not config.min_manual_duty <= config.default_manual_duty <= 100:
        raise ValueError("default_manual_duty 超出允许范围")
    if config.log_level not in LOG_LEVELS:
        raise ValueError(f"log_level 必须是 {'/'.join(LOG_LEVELS)} 之一")
    if config.ipmi_transport not in IPMI_TRANSPORTS:
        raise ValueError(f"ipmi_transport 必须是 {'/'.join(IPMI_TRANSPORTS)} 之一")
    if config.ipmi_network_backend not in IPMI_NETWORK_BACKENDS:
        raise ValueError(
            f"ipmi_network_backend 必须是 {'/'.join(IPMI_NETWORK_BACKENDS)} 之一"
        )
    if not 1 <= config.ipmi_port <= 65535:
        raise ValueError("ipmi_port 必须在 1–65535 之间")
    if not config.ipmi_interface.strip():
        raise ValueError("ipmi_interface 不能为空")
    if not config.ipmi_privilege.strip():
        raise ValueError("ipmi_privilege 不能为空")
    if not config.nvidia_smi_path.strip():
        raise ValueError("nvidia_smi_path 不能为空")
    if len(config.ipmi_channel_names) != 8:
        raise ValueError("EPYCD8 必须有 8 个 IPMI 控制字节")
    named = [name for name in config.ipmi_channel_names if name]
    if len(set(named)) != len(named):
        raise ValueError("ipmi_channel_names 中不得有重复的传感器名称")
    validate_mappings(config.mappings, config.ipmi_channel_names)
