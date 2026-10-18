import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from gpu_fan_controller.config import DEFAULT_AUTO_CURVE, load_config
from gpu_fan_controller.controller import FanController, interpolate_curve
from gpu_fan_controller.hardware import DryRunIpmi, HardwareError
from tests.util import MAPPINGS, config_dict, make_config


class FakeClock:
    def __init__(self, value: float = 1000.0):
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeGpuReader:
    def __init__(self, temps, buses=None, error=None):
        self.temps = dict(temps)
        self.buses = dict(buses or {})
        self.error = error

    def read_gpus(self):
        if self.error:
            raise HardwareError(self.error)
        readings = {}
        for index, temp in self.temps.items():
            bus = self.buses.get(index)
            if bus is None:
                bus = next(item.pci_bus_id for item in MAPPINGS if item.gpu_id == index)
            readings[index] = {
                "name": "Tesla T10",
                "pci_bus_id": bus,
                "temperature_c": temp,
            }
        return readings


class NoRpmIpmi(DryRunIpmi):
    def read_rpms(self):
        return {}


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        patcher = mock.patch("gpu_fan_controller.controller.time.monotonic", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def build(self, temps=None, *, control_enabled=False, dry_run=False, backend=None,
              buses=None, config_path=None, **overrides):
        config = make_config(control_enabled=control_enabled, **overrides)
        reader = FakeGpuReader(temps if temps is not None else {i: 60.0 for i in range(4)}, buses)
        backend = backend if backend is not None else DryRunIpmi(config)
        return FanController(
            config, reader, backend, dry_run=dry_run, config_path=config_path
        ), backend, reader

    def state(self, controller, gpu_id):
        return next(item for item in controller.snapshot()["gpus"] if item["id"] == gpu_id)

    # ---------------------------------------------------------------- 曲线

    def test_interpolate_curve(self):
        curve = make_config().auto_curve
        self.assertEqual(interpolate_curve(-10, curve), 30)
        self.assertEqual(interpolate_curve(0, curve), 30)
        self.assertEqual(interpolate_curve(60, curve), 40)
        self.assertEqual(interpolate_curve(70, curve), 55)
        self.assertEqual(interpolate_curve(80, curve), 85)
        self.assertEqual(interpolate_curve(90, curve), 100)
        self.assertEqual(interpolate_curve(150, curve), 100)

    def test_auto_curve_drives_fans(self):
        controller, backend, _ = self.build(dry_run=True)
        controller._tick()
        state = self.state(controller, 0)
        self.assertEqual(state["mode"], "auto")
        self.assertEqual(state["requested_duty"], 40)
        self.assertEqual(state["actual_duty"], 40)
        self.assertEqual(backend.duties[4], 40)
        self.assertEqual(backend.duties[2], 40)

    # ---------------------------------------------------------------- 安全逻辑

    def test_stale_temperature_forces_full_speed(self):
        controller, backend, reader = self.build(dry_run=True)
        controller._tick()
        self.assertFalse(self.state(controller, 0)["sensor_stale"])

        self.clock.advance(11)
        reader.error = "nvidia-smi 超时"
        controller._tick()

        state = self.state(controller, 0)
        self.assertTrue(state["sensor_stale"])
        self.assertEqual(state["requested_duty"], 100)
        self.assertIn("GPU 温度数据过期", state["alarm"])
        self.assertEqual(backend.duties[4], 100)

    def test_thermal_override_hysteresis(self):
        controller, backend, reader = self.build(dry_run=True, temps={i: 86.0 for i in range(4)})
        controller._tick()
        self.assertTrue(self.state(controller, 0)["thermal_override"])
        self.assertEqual(backend.duties[4], 100)

        reader.temps = {i: 80.0 for i in range(4)}
        self.clock.advance(1)
        controller._tick()
        self.assertTrue(self.state(controller, 0)["thermal_override"])

        reader.temps = {i: 77.0 for i in range(4)}
        self.clock.advance(1)
        controller._tick()
        state = self.state(controller, 0)
        self.assertFalse(state["thermal_override"])
        self.assertEqual(state["requested_duty"], 76)  # 75°C→70，80°C→85 之间插值

    def test_missing_rpm_blocks_writes(self):
        config = make_config()
        controller, backend, _ = self.build(dry_run=True, backend=NoRpmIpmi(config))
        before = list(backend.duties)
        controller._tick()
        self.assertEqual(backend.duties, before)

    def test_expect_rpm_false_allows_writes_without_tach(self):
        mapping = replace(MAPPINGS[0], expect_rpm=False)
        config = make_config(mappings=(mapping,))
        backend = NoRpmIpmi(config)
        controller = FanController(
            config, FakeGpuReader({0: 60.0}), backend, dry_run=True
        )
        controller._tick()
        self.assertEqual(backend.duties[4], 40)
        self.assertIsNone(self.state(controller, 0)["alarm"])

    def test_control_disabled_never_writes(self):
        controller, backend, _ = self.build(dry_run=False)
        before = list(backend.duties)
        controller._tick()
        self.assertFalse(controller.control_enabled)
        self.assertEqual(backend.duties, before)
        with self.assertRaises(ValueError):
            controller.set_control(0, "manual", 60)

    def test_missing_rpm_alarm_after_grace_period(self):
        config = make_config()
        controller, _, _ = self.build(dry_run=True, backend=NoRpmIpmi(config))
        self.clock.advance(11)
        controller._tick()
        state = self.state(controller, 0)
        self.assertIn("主板风扇无 RPM 读数", state["alarm"])
        self.assertEqual(state["requested_duty"], 100)

    def test_failsafe_on_stop_restores_auto(self):
        controller, backend, _ = self.build(dry_run=True)
        controller.stop()
        for channel in (4, 5, 7, 2):
            self.assertEqual(backend.duties[channel], 0)
        # 幂等：再次 stop 不会重复下发
        controller.stop()

    def test_failsafe_on_stop_can_use_100_percent(self):
        controller, backend, _ = self.build(dry_run=True, restore_auto_on_stop=False)
        controller.stop()
        for channel in (4, 5, 7, 2):
            self.assertEqual(backend.duties[channel], 100)

    def test_force_release_on_stop_ignores_restore_setting(self):
        controller, backend, _ = self.build(dry_run=True, restore_auto_on_stop=False)
        controller.stop(force_release=True)
        for channel in (4, 5, 7, 2):
            self.assertEqual(backend.duties[channel], 0)

    def test_no_write_after_stop_marker(self):
        controller, backend, _ = self.build(dry_run=True)
        before = list(backend.duties)
        controller._stop.set()
        controller._tick()
        self.assertEqual(backend.duties, before)

    # ---------------------------------------------------------------- 手动控制

    def test_manual_mode(self):
        controller, backend, _ = self.build(control_enabled=True, dry_run=False)
        controller._tick()
        controller.set_control(0, "manual", 55)
        self.assertEqual(self.state(controller, 0)["requested_duty"], 55)
        self.clock.advance(1.1)
        controller._tick()
        self.assertEqual(backend.duties[4], 55)
        self.assertEqual(self.state(controller, 0)["mode"], "manual")

        controller.set_control(0, "auto")
        self.clock.advance(1.1)
        controller._tick()
        self.assertEqual(self.state(controller, 0)["requested_duty"], 40)

    def test_manual_duty_validation(self):
        controller, _, _ = self.build(control_enabled=True, dry_run=False)
        with self.assertRaises(ValueError):
            controller.set_control(0, "manual", 19)
        with self.assertRaises(ValueError):
            controller.set_control(0, "manual", True)
        with self.assertRaises(ValueError):
            controller.set_control(0, "bogus")
        with self.assertRaises(KeyError):
            controller.set_control(99, "auto")
        controller.set_control(0, "manual", 100)
        self.assertEqual(self.state(controller, 0)["manual_duty"], 100)

    # ---------------------------------------------------------------- 映射

    def test_gpu_bound_by_pci_bus_id(self):
        buses = {0: "00000000:41:00.0", 1: "00000000:01:00.0"}
        controller, _, _ = self.build(
            dry_run=True,
            temps={0: 80.0, 1: 60.0, 2: 60.0, 3: 60.0},
            buses=buses,
        )
        controller._tick()
        self.assertEqual(self.state(controller, 0)["temperature_c"], 60.0)
        self.assertEqual(self.state(controller, 1)["temperature_c"], 80.0)
        self.assertIn("已按 PCI 地址绑定", controller.snapshot()["errors"]["mapping"])

    def test_missing_gpu_reported(self):
        controller, _, _ = self.build(
            dry_run=True, temps={0: 60.0, 1: 60.0, 2: 60.0}
        )
        controller._tick()
        mapping_error = controller.snapshot()["errors"]["mapping"]
        self.assertIn("未检测到 GPU 3", mapping_error)
        self.assertEqual(self.state(controller, 3)["requested_duty"], 100)

    def test_unconfigured_gpu_reported(self):
        controller, _, _ = self.build(
            dry_run=True,
            temps={0: 60.0, 1: 60.0, 2: 60.0, 3: 60.0, 4: 60.0},
            buses={4: "00000000:c1:00.0"},
        )
        controller._tick()
        self.assertIn("未配置的 GPU", controller.snapshot()["errors"]["mapping"])

    def test_snapshot_limits(self):
        controller, _, _ = self.build(dry_run=True)
        limits = controller.snapshot()["limits"]
        self.assertEqual(limits["min_manual_duty"], 20)
        self.assertEqual(limits["thermal_override_temp"], 85.0)
        self.assertEqual(len(limits["auto_curve"]), 7)

    # ---------------------------------------------------------------- 曲线编辑

    def test_set_auto_curve_applies_immediately(self):
        controller, backend, _ = self.build(dry_run=True)
        controller.set_auto_curve([[0, 30], [60, 50], [100, 60]])
        self.clock.advance(1.1)
        controller._tick()
        self.assertEqual(self.state(controller, 0)["requested_duty"], 50)
        self.assertEqual(backend.duties[4], 50)
        self.assertEqual(
            controller.snapshot()["limits"]["auto_curve"],
            [[0.0, 30], [60.0, 50], [100.0, 60]],
        )

    def test_set_auto_curve_invalid_rejected(self):
        controller, _, _ = self.build(dry_run=True)
        with self.assertRaises(ValueError):
            controller.set_auto_curve([[60, 40], [30, 30]])
        self.assertEqual(len(controller.snapshot()["limits"]["auto_curve"]), 7)

    def test_set_auto_curve_persists_to_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(config_dict()), encoding="utf-8")
            config = make_config()
            controller = FanController(
                config, FakeGpuReader({i: 60.0 for i in range(4)}),
                DryRunIpmi(config), config_path=str(path),
            )
            controller.set_auto_curve([[30, 45], [60, 60], [80, 95]])
            reloaded = load_config(str(path))
            self.assertEqual(reloaded.auto_curve, ((30.0, 45), (60.0, 60), (80.0, 95)))
            self.assertIsNone(controller.snapshot()["curve_save_error"])

    def test_set_auto_curve_save_failure_keeps_curve(self):
        with mock.patch(
            "gpu_fan_controller.controller.save_auto_curve",
            side_effect=OSError("read-only file system"),
        ):
            controller, _, _ = self.build(config_path="/nonexistent/config.json")
            controller.set_auto_curve([[30, 45], [60, 60]])
        snapshot = controller.snapshot()
        self.assertIn("read-only", snapshot["curve_save_error"])
        self.assertEqual(snapshot["limits"]["auto_curve"], [[30.0, 45], [60.0, 60]])

    def test_reset_auto_curve(self):
        controller, _, _ = self.build(dry_run=True)
        controller.set_auto_curve([[30, 45], [60, 60], [80, 90]])
        controller.reset_auto_curve()
        self.assertEqual(
            controller.snapshot()["limits"]["auto_curve"],
            [[temp, duty] for temp, duty in DEFAULT_AUTO_CURVE],
        )


if __name__ == "__main__":
    unittest.main()
