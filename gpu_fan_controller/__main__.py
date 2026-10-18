import argparse
import logging
import signal
import sys

from . import __version__
from .config import load_config, normalize_pci_bus_id
from .controller import FanController
from .hardware import (
    HardwareError,
    NvidiaSmi,
    create_ipmi_backend,
    resolve_ipmi_transport,
)
from .instance_lock import InstanceLock
from .web import serve

log = logging.getLogger("gpu_fan_controller")


def _raise_interrupt(signum, frame) -> None:
    raise KeyboardInterrupt


def _print_summary(config) -> None:
    print(f"配置文件校验通过（gpu_fan_controller {__version__}）")
    print(f"  监听地址：{config.host}:{config.port}")
    print(f"  控制模式：{'已启用（会实际改写风扇）' if config.control_enabled else '只读监控（control_enabled=false）'}")
    print(f"  IPMI 传输：{resolve_ipmi_transport(config)}（ipmi_transport={config.ipmi_transport}）")
    print(f"  采样间隔：{config.poll_interval}s，温度过期阈值：{config.stale_after}s")
    print(f"  热保护：{config.thermal_override_temp}°C 触发 / {config.thermal_release_temp}°C 解除")
    print(f"  手动占空比范围：{config.min_manual_duty}–100%")
    print("  映射：")
    for item in config.mappings:
        print(
            f"    GPU{item.gpu_id} {item.pci_bus_id} → {item.fan_name}（通道 {item.fan_channel}）"
        )


def _probe(config, gpu_reader, fan_backend) -> int:
    """只读探测：验证 nvidia-smi、IPMI 读数与映射，不写风扇。"""
    print("== GPU ==")
    try:
        readings = gpu_reader.read_gpus()
    except HardwareError as exc:
        print(f"  nvidia-smi 读取失败：{exc}")
        return 1
    for index in sorted(readings):
        item = readings[index]
        temp = item["temperature_c"]
        print(
            f"  索引 {index}: {item['name']}   PCI {item['pci_bus_id']}   "
            f"温度 {('%.1f°C' % temp) if temp is not None else 'N/A'}"
        )

    print("== GPU 映射核对 ==")
    ok = True
    for item in config.mappings:
        expected = normalize_pci_bus_id(item.pci_bus_id)
        match = [
            index
            for index, reading in readings.items()
            if normalize_pci_bus_id(reading["pci_bus_id"]) == expected
        ]
        if match:
            print(f"  GPU{item.gpu_id} {item.pci_bus_id} → nvidia-smi 索引 {match[0]} ✓")
        else:
            ok = False
            print(f"  GPU{item.gpu_id} {item.pci_bus_id} → 未找到对应 GPU ✗")

    print("== 主板风扇 ==")
    try:
        duties = fan_backend.read_duties()
        rpms = fan_backend.read_rpms()
    except HardwareError as exc:
        print(f"  IPMI 读取失败：{exc}")
        print("  提示：真实 IPMI 需要 root 或 /dev/ipmi* 的读写权限，见 README 的权限配置。")
        return 2
    for channel, name in enumerate(config.ipmi_channel_names):
        if not name:
            continue
        managed = [item for item in config.mappings if item.fan_channel == channel]
        mark = f" ← GPU{managed[0].gpu_id}" if managed else ""
        duty_text = "自动（未接管）" if duties[channel] == 0 else f"{duties[channel]}%"
        print(f"  通道 {channel} {name}: 占空比 {duty_text}  转速 {rpms.get(name)}{mark}")
    return 0 if ok else 3


def main() -> None:
    parser = argparse.ArgumentParser(prog="gpu-fan-controller")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--dry-run", action="store_true", help="使用模拟主板风扇，不执行真实控制")
    parser.add_argument("--check", action="store_true", help="只校验配置并打印映射，不启动服务")
    parser.add_argument("--probe", action="store_true", help="只读取 GPU/IPMI 状态并核对映射，不写风扇")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args()

    config = load_config(args.config)
    logging.basicConfig(
        level=getattr(logging, config.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    if args.check:
        _print_summary(config)
        return

    signal.signal(signal.SIGTERM, _raise_interrupt)
    gpu_reader = NvidiaSmi(config.command_timeout, config.nvidia_smi_path)
    try:
        fan_backend = create_ipmi_backend(config, dry_run=args.dry_run)
    except HardwareError as exc:
        log.error("初始化 IPMI 后端失败：%s", exc)
        raise SystemExit(1) from exc
    if args.probe:
        raise SystemExit(_probe(config, gpu_reader, fan_backend))
    lock = InstanceLock()
    if not lock.acquire():
        log.error(
            "检测到另一个 GPU 风扇控制器实例正在运行；请先停止桌面程序或其他服务。"
        )
        raise SystemExit(2)
    controller = FanController(
        config, gpu_reader, fan_backend, dry_run=args.dry_run, config_path=args.config
    )

    log.info("Tesla T10 风扇控制界面：http://%s:%d", config.host, config.port)
    log.info("IPMI 后端：%s", getattr(fan_backend, "transport_label", "unknown"))
    if config.control_enabled and not args.dry_run:
        log.warning("control_enabled=true：将向主板下发真实风扇控制命令")
    elif not args.dry_run:
        log.info("control_enabled=false：当前仅监控，不写风扇")
    try:
        serve(controller, config.host, config.port)
    except KeyboardInterrupt:
        log.info("收到退出信号，正在停止")
    except OSError as exc:
        log.error("启动 HTTP 服务失败：%s", exc)
        raise SystemExit(1) from exc
    finally:
        lock.release()


if __name__ == "__main__":
    main()
