import copy
import json
import logging
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ..config import (
    DEFAULT_AUTO_CURVE,
    AppConfig,
    default_raw_config,
    normalize_mappings,
    parse_config,
    save_config,
    validate_curve,
)
from ..controller import FanController
from ..hardware import (
    HardwareError,
    NvidiaSmi,
    create_ipmi_backend,
    resolve_ipmi_transport,
)

log = logging.getLogger(__name__)


class HostError(RuntimeError):
    pass


class ControllerHost:
    """管理配置、IPMI 后端和控制线程的生命周期，供桌面 GUI 使用。"""

    def __init__(self, config_path: str | Path, dry_run: bool = False):
        self.config_path = Path(config_path)
        self.dry_run = dry_run
        self._lock = threading.RLock()
        self._started = False
        self._last_save_error: str | None = None

        if not self.config_path.exists():
            save_config(str(self.config_path), default_raw_config())
            log.info("已创建默认配置：%s", self.config_path)

        self._raw = self._read_raw()
        self._config = parse_config(self._raw)
        self._gpu_reader = NvidiaSmi(self._config.command_timeout, self._config.nvidia_smi_path)
        self._backend = create_ipmi_backend(self._config, dry_run=self.dry_run)
        self._controller = self._build_controller(self._config, self._backend, self._gpu_reader)

    # ---------------------------------------------------------------- 基础属性

    @property
    def config(self) -> AppConfig:
        return self._config

    @property
    def raw_config(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._raw)

    @property
    def controller(self) -> FanController:
        return self._controller

    @property
    def backend(self):
        return self._backend

    @property
    def last_save_error(self) -> str | None:
        return self._last_save_error

    def resolved_transport(self) -> str:
        return resolve_ipmi_transport(self._config)

    @property
    def ipmi_access_warning(self) -> str | None:
        if self.dry_run or self.resolved_transport() != "local":
            return None
        for path in ("/dev/ipmi0", "/dev/ipmi/0", "/dev/ipmidev/0"):
            if not os.path.exists(path):
                continue
            if os.access(path, os.R_OK | os.W_OK):
                return None
            if os.geteuid() == 0:
                return None
            try:
                import grp
                import stat

                info = os.stat(path)
                mode = stat.filemode(info.st_mode)
                group = grp.getgrgid(info.st_gid).gr_name
            except (ImportError, KeyError, OSError):
                info = None
                mode = "?"
                group = "?"
            if info is not None and info.st_gid not in os.getgroups() and info.st_gid != os.getegid():
                return (
                    f"{path} 属于 {group} 组（{mode}），当前会话不在该组；"
                    f"请重新登录，或执行 newgrp {group} 后从该终端启动桌面程序。"
                )
            return (
                f"{path} 权限不足（{group} 组，{mode}）；请执行 "
                f"sudo chgrp ipmi {path} && sudo chmod 0660 {path}，"
                "并检查 /etc/udev/rules.d/99-ipmi.rules 是否包含 "
                'KERNEL=="ipmi*", GROUP="ipmi", MODE="0660"'
            )
        return "找不到 /dev/ipmi0；如果使用网络 IPMI，请在设置中把传输方式改为 network。"

    # ---------------------------------------------------------------- 生命周期

    def start(self) -> None:
        with self._lock:
            if not self._started:
                self._controller.start()
                self._started = True

    def stop(self) -> None:
        with self._lock:
            if self._started:
                self._controller.stop()
                self._started = False
            else:
                self._controller.stop(failsafe=False)

    def snapshot(self) -> dict[str, Any]:
        snapshot = self._controller.snapshot()
        snapshot["ipmi_transport"] = self._backend.transport_label
        snapshot["config_path"] = str(self.config_path)
        return snapshot

    # ---------------------------------------------------------------- 控制代理

    def set_control(self, gpu_id: int, mode: str, duty_percent=None) -> None:
        self._controller.set_control(gpu_id, mode, duty_percent)

    def set_auto_curve(self, curve) -> None:
        self._controller.set_auto_curve(curve)
        validated = validate_curve(curve)
        with self._lock:
            self._raw["auto_curve"] = [[temp, duty] for temp, duty in validated]

    def reset_auto_curve(self) -> None:
        self._controller.reset_auto_curve()
        with self._lock:
            self._raw["auto_curve"] = [
                [temp, duty] for temp, duty in DEFAULT_AUTO_CURVE
            ]

    def probe_gpus(self) -> dict[int, dict[str, Any]]:
        return self._gpu_reader.read_gpus()

    def enumerate_fans(self) -> dict[str, Any]:
        return self._backend.enumerate_fans()

    # ---------------------------------------------------------------- 测试/应用配置

    def candidate_report(self, raw: dict) -> dict[str, Any]:
        """用候选配置建立独立后端做一次只读测试，不影响当前控制器。"""
        config = parse_config(raw)
        backend = create_ipmi_backend(config, dry_run=self.dry_run)
        duties = backend.read_duties()
        rpms = backend.read_rpms()
        return {
            "transport": backend.transport_label,
            "duties": duties,
            "rpms": rpms,
        }

    def apply_raw(self, new_raw: dict) -> dict[str, Any]:
        """校验、预检、重建控制器并保存配置。

        先构建新后端并做一次只读预检；预检失败时当前控制器完全不受影响。
        预检通过后停止旧控制器（把旧受管通道交还 BMC 自动），启动新控制器，
        最后写回配置文件。返回 {"config": AppConfig, "save_error": str|None}。
        """
        new_raw = copy.deepcopy(new_raw)
        new_raw["config_version"] = 2
        new_config = parse_config(new_raw)
        new_reader = NvidiaSmi(new_config.command_timeout, new_config.nvidia_smi_path)
        new_backend = create_ipmi_backend(new_config, dry_run=self.dry_run)
        if not self.dry_run:
            new_backend.read_duties()
        new_controller = self._build_controller(new_config, new_backend, new_reader)

        with self._lock:
            old_controller = self._controller
            release_error = old_controller.stop(force_release=True)
            self._raw = new_raw
            self._config = new_config
            self._gpu_reader = new_reader
            self._backend = new_backend
            self._controller = new_controller
            new_controller.start()
            self._started = True
            save_error = None
            try:
                save_config(str(self.config_path), new_raw)
            except OSError as exc:
                save_error = str(exc)
                log.error("配置已应用但保存失败：%s", exc)
            self._last_save_error = save_error
        log.warning(
            "控制器已重启：IPMI=%s，映射 %s",
            new_backend.transport_label,
            "，".join(
                f"GPU{item.gpu_id}→ch{item.fan_channel}"
                for item in new_config.mappings
            ),
        )
        if release_error:
            log.error("旧通道交还 BMC 自动控制失败：%s", release_error)
        return {
            "config": new_config,
            "save_error": save_error,
            "release_error": release_error,
        }

    def apply_mappings(self, mappings) -> dict[str, Any]:
        normalized = normalize_mappings(mappings)
        raw = self.raw_config
        raw["mappings"] = [
            {
                "gpu_id": item.gpu_id,
                "pci_bus_id": item.pci_bus_id,
                "fan_name": item.fan_name,
                "fan_channel": item.fan_channel,
                "expect_rpm": item.expect_rpm,
            }
            for item in normalized
        ]
        return self.apply_raw(raw)

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        raw = self.raw_config
        raw.update(values)
        return self.apply_raw(raw)

    # ---------------------------------------------------------------- 冲突检测

    def web_service_running(self, timeout: float = 1.0) -> bool:
        host = self._config.host
        if host in {"0.0.0.0", "::", ""}:
            host = "127.0.0.1"
        url = f"http://{host}:{self._config.port}/api/health"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return response.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    # ---------------------------------------------------------------- 内部

    def _read_raw(self) -> dict:
        try:
            return json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HostError(f"无法读取配置文件 {self.config_path}：{exc}") from exc

    def _build_controller(self, config, backend, gpu_reader):
        return FanController(
            config,
            gpu_reader,
            backend,
            dry_run=self.dry_run,
            config_path=str(self.config_path),
        )
