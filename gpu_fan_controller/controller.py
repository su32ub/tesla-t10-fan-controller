import copy
import logging
import threading
import time
from typing import Any

from .config import (
    DEFAULT_AUTO_CURVE,
    AppConfig,
    normalize_pci_bus_id,
    save_auto_curve,
    validate_curve,
)
from .hardware import HardwareError

log = logging.getLogger(__name__)

ERROR_LABELS = {
    "gpu": "GPU 采样",
    "ipmi": "BMC/IPMI",
    "control": "风扇控制",
    "mapping": "GPU 映射",
}


def interpolate_curve(temp_c: float, curve: tuple[tuple[float, int], ...]) -> int:
    if temp_c <= curve[0][0]:
        return curve[0][1]
    if temp_c >= curve[-1][0]:
        return curve[-1][1]
    for (left_temp, left_duty), (right_temp, right_duty) in zip(curve, curve[1:]):
        if left_temp <= temp_c <= right_temp:
            ratio = (temp_c - left_temp) / (right_temp - left_temp)
            return int(round(left_duty + ratio * (right_duty - left_duty)))
    return curve[-1][1]


class FanController:
    def __init__(
        self,
        config: AppConfig,
        gpu_reader: Any,
        fan_backend: Any,
        dry_run: bool = False,
        config_path: str | None = None,
    ):
        self.config = config
        self.gpu_reader = gpu_reader
        self.fan_backend = fan_backend
        self.dry_run = dry_run
        self.config_path = config_path
        self.control_enabled = dry_run or config.control_enabled
        self._auto_curve = config.auto_curve
        self._curve_save_error: str | None = None
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._stop_complete = threading.Event()
        self._stop_error: str | None = None
        self._thread: threading.Thread | None = None
        self._started_at = time.monotonic()
        self._temperature_success: dict[int, float] = {}
        self._last_write: float | None = None
        self._write_count = 0
        self._stopped = False
        self._errors: dict[str, str | None] = {
            "gpu": None,
            "ipmi": None,
            "control": None,
            "mapping": None,
        }
        self._bus_to_gpu = {
            normalize_pci_bus_id(item.pci_bus_id): item.gpu_id for item in config.mappings
        }
        self._states = {
            item.gpu_id: {
                "id": item.gpu_id,
                "name": "Tesla T10",
                "pci_bus_id": item.pci_bus_id,
                "temperature_c": None,
                "temperature_age_seconds": None,
                "fan_name": item.fan_name,
                "fan_channel": item.fan_channel,
                "fan_rpm": None,
                "expect_rpm": item.expect_rpm,
                "mode": "auto",
                "manual_duty": config.default_manual_duty,
                "requested_duty": 100,
                "actual_duty": None,
                "thermal_override": False,
                "sensor_stale": True,
                "alarm": "等待 GPU 温度数据",
            }
            for item in config.mappings
        }

    # ------------------------------------------------------------------ 生命周期

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="fan-controller", daemon=True)
            self._thread.start()

    def stop(
        self,
        timeout: float | None = None,
        failsafe: bool = True,
        force_release: bool = False,
    ) -> str | None:
        """停止控制线程。

        force_release=True 用于配置/映射重载：无论 restore_auto_on_stop 如何，
        都把旧受管通道交还 BMC 自动控制，避免被移除的通道停留在 100%。
        返回失败保护命令的错误信息（成功为 None）。
        """
        with self._lock:
            already_stopping = self._stopped
            if not already_stopping:
                self._stopped = True
        if already_stopping:
            wait_timeout = (
                timeout
                if timeout is not None
                else max(35.0, self.config.command_timeout * 5 + 5)
            )
            self._stop_complete.wait(wait_timeout)
            return self._stop_error
        self._stop.set()
        self._wake.set()
        if timeout is None:
            timeout = max(5.0, self.config.command_timeout + 3.0)
        error = None
        try:
            if self._thread is not None:
                self._thread.join(timeout)
                if self._thread.is_alive():
                    log.warning(
                        "控制线程在 %.1fs 内未退出；将继续执行失败保护，旧线程不会再写风扇",
                        timeout,
                    )
            if failsafe:
                error = self._apply_failsafe(force_release=force_release)
        finally:
            self._stop_error = error
            self._stop_complete.set()
        return error

    def _apply_failsafe(self, force_release: bool = False) -> str | None:
        if not self.control_enabled:
            log.info("控制未启用，退出时不下发失败保护命令")
            return None
        channels = sorted({state["fan_channel"] for state in self._states.values()})
        with self._write_lock:
            if force_release or self.config.restore_auto_on_stop:
                try:
                    self.fan_backend.release_control(set(channels))
                    log.warning("退出失败保护：风扇通道 %s 已交还 BMC 自动控制", channels)
                    return None
                except HardwareError as exc:
                    log.error("退出时交还 BMC 自动控制失败：%s", exc)
                    return str(exc)
            try:
                current = self.fan_backend.read_duties()
                changes = {channel: 100 for channel in channels if current[channel] != 100}
                if not changes:
                    log.info("退出失败保护：受管风扇已经是 100%%")
                    return None
                self.fan_backend.write_duties(changes)
                log.warning("退出失败保护：风扇通道 %s 已设为 100%%", sorted(changes))
                return None
            except HardwareError as exc:
                log.error("退出时下发失败保护命令失败：%s", exc)
                return str(exc)

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self._tick()
            except Exception as exc:  # 控制循环绝不能因单次异常退出
                log.exception("控制循环异常")
                with self._lock:
                    self._set_error_locked("control", f"控制循环异常：{exc}")
            elapsed = time.monotonic() - started
            self._wake.wait(max(0.25, self.config.poll_interval - elapsed))
            self._wake.clear()

    # ------------------------------------------------------------------ 采样与决策

    def _tick(self) -> None:
        gpu_readings = None
        fan_duties = None
        fan_rpms = None
        gpu_error = None
        ipmi_error = None
        try:
            gpu_readings = self.gpu_reader.read_gpus()
        except HardwareError as exc:
            gpu_error = str(exc)
        try:
            fan_duties = self.fan_backend.read_duties()
            fan_rpms = self.fan_backend.read_rpms()
        except HardwareError as exc:
            ipmi_error = str(exc)

        now = time.monotonic()
        with self._lock:
            self._set_error_locked("gpu", gpu_error)
            self._set_error_locked("ipmi", ipmi_error)
            if gpu_readings is not None:
                mapping_error = self._apply_gpu_readings_locked(gpu_readings, now)
                self._set_error_locked("mapping", mapping_error)
            if fan_duties is not None:
                for state in self._states.values():
                    state["actual_duty"] = fan_duties[state["fan_channel"]]
                    state["fan_rpm"] = fan_rpms.get(state["fan_name"]) if fan_rpms is not None else None

            desired: dict[int, int] = {}
            missing_managed_rpm = fan_duties is not None and any(
                state["fan_rpm"] is None and state["expect_rpm"]
                for state in self._states.values()
            )
            for state in self._states.values():
                self._update_desired_locked(state, now)
                actual = state["actual_duty"]
                requested = state["requested_duty"]
                if actual is None or actual == requested:
                    continue
                # 自动模式下的 1–2% 抖动不值得写 BMC
                if (
                    state["mode"] == "auto"
                    and not state["sensor_stale"]
                    and not state["thermal_override"]
                    and abs(actual - requested) < 2
                ):
                    continue
                desired[state["fan_channel"]] = requested
            write_allowed = (
                self.control_enabled
                and fan_duties is not None
                and not missing_managed_rpm
                and bool(desired)
                and (self._last_write is None or now - self._last_write >= 1.0)
            )

        if not write_allowed:
            return
        with self._write_lock:
            if self._stop.is_set():
                return
            try:
                confirmed = self.fan_backend.write_duties(desired)
            except HardwareError as exc:
                log.error("写入风扇占空比失败：%s", exc)
                with self._lock:
                    self._set_error_locked("control", str(exc))
                return
            with self._lock:
                self._set_error_locked("control", None)
                self._last_write = time.monotonic()
                self._write_count += 1
                for state in self._states.values():
                    state["actual_duty"] = confirmed[state["fan_channel"]]
        log.info("风扇占空比已更新：%s", _format_channels(desired))

    def _apply_gpu_readings_locked(self, readings: dict[int, dict[str, Any]], now: float) -> str | None:
        """按 PCI 地址把 nvidia-smi 读数绑定到配置里的 GPU，返回映射异常描述。"""
        issues: list[str] = []
        matched: set[int] = set()
        for index in sorted(readings):
            reading = readings[index]
            bus = normalize_pci_bus_id(str(reading.get("pci_bus_id") or ""))
            gpu_id = self._bus_to_gpu.get(bus)
            if gpu_id is None:
                if index in self._states:
                    gpu_id = index
                    issues.append(f"PCI {bus or '未知'} 未出现在配置中，已按 nvidia-smi 索引 {index} 绑定")
            elif gpu_id != index:
                issues.append(
                    f"nvidia-smi 索引 {index} 的 PCI 地址 {bus} 在配置中属于 GPU {gpu_id}，已按 PCI 地址绑定"
                )
            if gpu_id is None:
                if index not in self._states:
                    issues.append(f"检测到未配置的 GPU：索引 {index}（PCI {bus or '未知'}）")
                continue
            state = self._states.get(gpu_id)
            if state is None:
                continue
            if gpu_id in matched:
                issues.append(f"多块 GPU 同时匹配到 GPU {gpu_id}（PCI {bus}）")
                continue
            matched.add(gpu_id)
            name = str(reading.get("name") or "").strip()
            if name and name not in {"N/A", "[N/A]"}:
                state["name"] = name
            if bus and bus not in {"n/a", "[n/a]", ""}:
                state["pci_bus_id"] = reading["pci_bus_id"]
            temperature = reading.get("temperature_c")
            state["temperature_c"] = temperature
            if temperature is not None:
                self._temperature_success[gpu_id] = now
        for gpu_id, state in self._states.items():
            if gpu_id not in matched:
                issues.append(f"未检测到 GPU {gpu_id}（配置 PCI {state['pci_bus_id']}）")
        return "；".join(issues) if issues else None

    def _update_desired_locked(self, state: dict[str, Any], now: float) -> None:
        gpu_id = state["id"]
        last_success = self._temperature_success.get(gpu_id)
        temp = state["temperature_c"]
        stale = temp is None or last_success is None or now - last_success > self.config.stale_after
        state["sensor_stale"] = stale
        if not stale:
            if temp >= self.config.thermal_override_temp:
                if not state["thermal_override"]:
                    log.warning(
                        "GPU%d 温度 %.1f°C ≥ %.1f°C，进入热保护（100%%）",
                        gpu_id,
                        temp,
                        self.config.thermal_override_temp,
                    )
                state["thermal_override"] = True
            elif state["thermal_override"] and temp <= self.config.thermal_release_temp:
                log.warning(
                    "GPU%d 温度 %.1f°C ≤ %.1f°C，退出热保护",
                    gpu_id,
                    temp,
                    self.config.thermal_release_temp,
                )
                state["thermal_override"] = False

        alarms: list[str] = []
        if stale:
            alarms.append("GPU 温度数据过期")
        fan_rpm = state["fan_rpm"]
        fan_grace_elapsed = now - self._started_at >= 10.0
        if state["expect_rpm"] and fan_grace_elapsed and fan_rpm is None:
            alarms.append("主板风扇无 RPM 读数")
        elif state["expect_rpm"] and fan_grace_elapsed and fan_rpm <= 0:
            alarms.append("主板风扇停转")
        state["alarm"] = "；".join(alarms) if alarms else None

        if stale or state["thermal_override"] or alarms:
            state["requested_duty"] = 100
        elif state["mode"] == "manual":
            state["requested_duty"] = state["manual_duty"]
        else:
            state["requested_duty"] = interpolate_curve(temp, self._auto_curve)

    def _set_error_locked(self, key: str, value: str | None) -> None:
        if self._errors.get(key) == value:
            return
        label = ERROR_LABELS.get(key, key)
        if value:
            log.warning("%s错误：%s", label, value)
        else:
            log.info("%s错误已恢复", label)
        self._errors[key] = value

    # ------------------------------------------------------------------ 外部接口

    def set_control(self, gpu_id: int, mode: str, duty_percent: int | float | None = None) -> None:
        if not self.control_enabled:
            raise ValueError("实际风扇控制尚未启用")
        if mode not in {"auto", "manual"}:
            raise ValueError("mode 必须是 auto 或 manual")
        with self._lock:
            state = self._states.get(gpu_id)
            if state is None:
                raise KeyError(f"未知 GPU：{gpu_id}")
            previous_mode = state["mode"]
            state["mode"] = mode
            if mode == "manual" and duty_percent is not None:
                if isinstance(duty_percent, bool) or not isinstance(duty_percent, (int, float)):
                    raise ValueError("duty_percent 必须是数字")
                duty = int(round(duty_percent))
                if not self.config.min_manual_duty <= duty <= 100:
                    raise ValueError(f"手动占空比必须在 {self.config.min_manual_duty}–100 之间")
                state["manual_duty"] = duty
                if not state["sensor_stale"] and not state["thermal_override"] and not state["alarm"]:
                    state["requested_duty"] = duty
            if previous_mode != mode:
                log.info("GPU%d 控制模式切换为 %s", gpu_id, mode)
        self._wake.set()

    def set_auto_curve(self, curve) -> None:
        """热更新自动曲线，并尽力写回配置文件。"""
        validated = validate_curve(curve)
        with self._lock:
            self._auto_curve = validated
            log.info("自动曲线已更新：%s", _format_curve(validated))
            if self.config_path and not self.dry_run:
                try:
                    save_auto_curve(self.config_path, validated)
                except (OSError, ValueError) as exc:
                    self._curve_save_error = str(exc)
                    log.error("自动曲线已生效但保存失败：%s", exc)
                else:
                    self._curve_save_error = None
                    log.info("自动曲线已写入 %s", self.config_path)
            else:
                self._curve_save_error = None
        self._wake.set()

    def reset_auto_curve(self) -> None:
        """恢复出厂默认曲线并写回配置文件。"""
        self.set_auto_curve(DEFAULT_AUTO_CURVE)
        log.warning("自动曲线已恢复默认值")

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            states = copy.deepcopy([self._states[key] for key in sorted(self._states)])
            errors = dict(self._errors)
            last_write = self._last_write
            write_count = self._write_count
            success_times = dict(self._temperature_success)
        now = time.monotonic()
        for state in states:
            last_success = success_times.get(state["id"])
            state["temperature_age_seconds"] = (
                max(0.0, now - last_success) if last_success is not None else None
            )
        return {
            "timestamp": time.time(),
            "dry_run": self.dry_run,
            "control_enabled": self.control_enabled,
            "errors": errors,
            "last_write_age_seconds": (
                max(0.0, now - last_write) if last_write is not None else None
            ),
            "write_count": write_count,
            "curve_save_error": self._curve_save_error,
            "limits": {
                "min_manual_duty": self.config.min_manual_duty,
                "default_manual_duty": self.config.default_manual_duty,
                "poll_interval": self.config.poll_interval,
                "stale_after": self.config.stale_after,
                "thermal_override_temp": self.config.thermal_override_temp,
                "thermal_release_temp": self.config.thermal_release_temp,
                "auto_curve": [[temp, duty] for temp, duty in self._auto_curve],
            },
            "gpus": states,
        }


def _format_channels(changes: dict[int, int]) -> str:
    return "，".join(f"通道 {channel}→{duty}%" for channel, duty in sorted(changes.items()))


def _format_curve(curve: tuple[tuple[float, int], ...]) -> str:
    return "，".join(f"{temp:g}°C→{duty}%" for temp, duty in curve)
