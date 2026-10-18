import dataclasses

from gpu_fan_controller.config import AppConfig, GpuMapping

CURVE = (
    (0.0, 30),
    (60.0, 40),
    (70.0, 55),
    (75.0, 70),
    (80.0, 85),
    (85.0, 100),
    (100.0, 100),
)

CHANNEL_NAMES = (
    "CPU1_FAN1",
    None,
    "REAR_FAN1",
    "REAR_FAN2",
    "FRNT_FAN1",
    "FRNT_FAN2",
    "FRNT_FAN3",
    "FRNT_FAN4",
)

MAPPINGS = (
    GpuMapping(0, "00000000:01:00.0", "FRNT_FAN1", 4),
    GpuMapping(1, "00000000:41:00.0", "FRNT_FAN2", 5),
    GpuMapping(2, "00000000:81:00.0", "FRNT_FAN4", 7),
    GpuMapping(3, "00000000:82:00.0", "REAR_FAN1", 2),
)


def make_config(**overrides) -> AppConfig:
    values = dict(
        host="127.0.0.1",
        port=8080,
        poll_interval=1.0,
        stale_after=10.0,
        thermal_override_temp=85.0,
        thermal_release_temp=78.0,
        min_manual_duty=20,
        default_manual_duty=60,
        command_timeout=2.0,
        control_enabled=False,
        ipmitool_path="ipmitool",
        ipmi_channel_names=CHANNEL_NAMES,
        auto_curve=CURVE,
        mappings=MAPPINGS,
        log_level="INFO",
        restore_auto_on_stop=True,
    )
    values.update(overrides)
    return AppConfig(**values)


def config_dict(**overrides) -> dict:
    """把测试用的 AppConfig 转成可写入 JSON 的原始配置。"""
    config = make_config(**overrides)
    raw = dataclasses.asdict(config)
    raw["ipmi_channel_names"] = list(raw["ipmi_channel_names"])
    raw["auto_curve"] = [list(point) for point in raw["auto_curve"]]
    return raw
