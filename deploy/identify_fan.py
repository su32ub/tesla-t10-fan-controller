#!/usr/bin/env python3
"""风扇通道 / GPU 对应关系测试工具。

把指定 IPMI 风扇通道临时设为指定占空比，定期打印 GPU 温度与风扇 RPM，
结束后自动恢复该通道原来的占空比（0 表示交还 BMC 自动）。

用法示例（需要 root 或 ipmi 组权限）：
    python3 deploy/identify_fan.py --channel 3 --duty 100 --seconds 150
    sudo python3 deploy/identify_fan.py --channel 6 --duty 100 --seconds 150

注意：如果 gpu-fan-controller 服务正在接管该通道，服务可能在下一个周期
覆盖本次设置。建议只测试未接管的通道，或先停止服务。
"""

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path


def run(command: list[str], timeout: float = 8.0) -> str:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"{' '.join(command)} 失败：{detail}")
    return result.stdout


def read_duties(ipmitool: str) -> list[int]:
    output = run([ipmitool, "raw", "0x3a", "0x02"])
    return [int(token, 16) for token in output.split()]


def write_duties(ipmitool: str, duties: list[int]) -> None:
    if len(duties) != 8 or any(not 0 <= duty <= 100 for duty in duties):
        raise ValueError(f"占空比数组无效：{duties}")
    run([ipmitool, "raw", "0x3a", "0x01", *(f"0x{duty:02x}" for duty in duties)])


def gpu_temps(nvidia_smi: str) -> list[float | None]:
    output = run([
        nvidia_smi,
        "--query-gpu=index,temperature.gpu",
        "--format=csv,noheader,nounits",
    ])
    temps: list[float | None] = []
    for line in output.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 2:
            continue
        try:
            temps.append(float(parts[1]))
        except ValueError:
            temps.append(None)
    return temps


def fan_rpms(ipmitool: str, names: list[str]) -> dict[str, str]:
    output = run([ipmitool, "sensor"])
    readings: dict[str, str] = {}
    for line in output.splitlines():
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 2 or parts[0] not in names:
            continue
        readings[parts[0]] = parts[1]
    return readings


def main() -> int:
    parser = argparse.ArgumentParser(description="测试某个 IPMI 风扇通道对 GPU 温度的影响")
    parser.add_argument("--config", default=str(Path(__file__).resolve().parent.parent / "config.json"))
    parser.add_argument("--channel", type=int, required=True, help="风扇通道 0–7")
    parser.add_argument("--duty", type=int, required=True, help="测试占空比 0–100")
    parser.add_argument("--seconds", type=float, default=150, help="采样时长（秒）")
    parser.add_argument("--interval", type=float, default=10, help="采样间隔（秒）")
    parser.add_argument("--no-restore", action="store_true", help="结束后不恢复原占空比")
    args = parser.parse_args()

    if not 0 <= args.channel <= 7:
        parser.error("channel 必须在 0–7 之间")
    if not 0 <= args.duty <= 100:
        parser.error("duty 必须在 0–100 之间")

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    names = config.get("ipmi_channel_names", [])
    ipmitool = shutil.which(config.get("ipmitool_path", "ipmitool")) or "ipmitool"
    nvidia_smi = shutil.which("nvidia-smi") or "nvidia-smi"
    watched = [name for name in names if name]

    before = read_duties(ipmitool)
    target = list(before)
    target[args.channel] = args.duty
    channel_name = names[args.channel] if args.channel < len(names) else "?"

    print(f"测试通道 {args.channel}（{channel_name}）：{before[args.channel]}% → {args.duty}%")
    print(f"其余通道保持当前值：{before}")
    print("按 Ctrl+C 可提前结束并恢复。\n")
    write_duties(ipmitool, target)

    start = time.monotonic()
    try:
        while time.monotonic() - start < args.seconds:
            elapsed = time.monotonic() - start
            temps = gpu_temps(nvidia_smi)
            rpms = fan_rpms(ipmitool, watched)
            temp_text = "  ".join(
                f"GPU{index}={('%.0f°C' % temp) if temp is not None else 'N/A'}"
                for index, temp in enumerate(temps)
            )
            rpm_text = "  ".join(f"{name}={rpms.get(name, '?')}" for name in watched)
            print(f"[{elapsed:5.0f}s] {temp_text}")
            print(f"         {rpm_text}", flush=True)
            time.sleep(max(1.0, args.interval))
    except KeyboardInterrupt:
        print("\n收到中断。")
    finally:
        if args.no_restore:
            print(f"未恢复通道 {args.channel}，当前值为 {args.duty}%。")
        else:
            restored = list(read_duties(ipmitool))
            restored[args.channel] = before[args.channel]
            write_duties(ipmitool, restored)
            print(f"已恢复通道 {args.channel} 为 {before[args.channel]}%。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        print("提示：需要 root 或 /dev/ipmi* 的 ipmi 组读写权限。", file=sys.stderr)
        raise SystemExit(1)
