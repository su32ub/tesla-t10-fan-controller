import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from gpu_fan_controller.config import GpuMapping
from gpu_fan_controller.desktop.host import ControllerHost
from gpu_fan_controller.hardware import DryRunIpmi, HardwareError
from tests.util import config_dict

GPU_READINGS = {
    0: {"name": "Tesla T10", "pci_bus_id": "00000000:01:00.0", "temperature_c": 60.0},
    1: {"name": "Tesla T10", "pci_bus_id": "00000000:41:00.0", "temperature_c": 60.0},
    2: {"name": "Tesla T10", "pci_bus_id": "00000000:81:00.0", "temperature_c": 60.0},
    3: {"name": "Tesla T10", "pci_bus_id": "00000000:82:00.0", "temperature_c": 60.0},
}


class FakeSmi:
    def __init__(self, *_args, **_kwargs):
        pass

    def read_gpus(self):
        return dict(GPU_READINGS)


class HostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"
        self.path.write_text(json.dumps(config_dict()), encoding="utf-8")
        self.backends = []

        def fake_create(config, dry_run=False):
            backend = DryRunIpmi(config)
            self.backends.append(backend)
            return backend

        self.create_patch = mock.patch(
            "gpu_fan_controller.desktop.host.create_ipmi_backend", side_effect=fake_create
        )
        self.smi_patch = mock.patch("gpu_fan_controller.desktop.host.NvidiaSmi", FakeSmi)
        self.create_patch.start()
        self.smi_patch.start()
        self.host = ControllerHost(str(self.path), dry_run=True)
        self.host.start()

    def tearDown(self):
        self.host.stop()
        self.smi_patch.stop()
        self.create_patch.stop()
        self.tmp.cleanup()

    def test_apply_mappings_releases_old_channels_and_recreates_backend(self):
        first_backend = self.host.backend
        mappings = [
            GpuMapping(0, "00000000:01:00.0", "FRNT_FAN2", 5),
            GpuMapping(1, "00000000:41:00.0", "FRNT_FAN1", 4),
            GpuMapping(2, "00000000:81:00.0", "FRNT_FAN4", 7),
            GpuMapping(3, "00000000:82:00.0", "REAR_FAN1", 2),
        ]
        result = self.host.apply_mappings(mappings)
        self.assertIsNone(result["save_error"])
        self.assertIsNone(result["release_error"])
        self.assertIsNot(first_backend, self.host.backend)
        for channel in (4, 5, 7, 2):
            self.assertEqual(first_backend.duties[channel], 0)
        self.assertEqual(self.host.config.mappings[0].fan_channel, 5)
        self.assertEqual(
            sorted(self.host.backend.managed_channels), [2, 4, 5, 7]
        )

    def test_invalid_mapping_keeps_current_controller(self):
        old_controller = self.host.controller
        old_backend = self.host.backend
        with self.assertRaises(ValueError):
            self.host.apply_mappings(
                [
                    GpuMapping(0, "00000000:01:00.0", "FRNT_FAN2", 5),
                    GpuMapping(1, "00000000:41:00.0", "FRNT_FAN1", 5),
                ]
            )
        self.assertIs(self.host.controller, old_controller)
        self.assertIs(self.host.backend, old_backend)

    def test_preflight_failure_keeps_current_controller(self):
        old_controller = self.host.controller
        old_backend = self.host.backend
        raw = self.host.raw_config
        with mock.patch(
            "gpu_fan_controller.desktop.host.create_ipmi_backend",
            side_effect=HardwareError("BMC unreachable"),
        ):
            with self.assertRaises(HardwareError):
                self.host.apply_raw(raw)
        self.assertIs(self.host.controller, old_controller)
        self.assertIs(self.host.backend, old_backend)

    def test_snapshot_includes_transport(self):
        snapshot = self.host.snapshot()
        self.assertEqual(snapshot["ipmi_transport"], "dry-run")
        self.assertIn("limits", snapshot)

    def test_curve_edit_is_merged_into_later_settings_save(self):
        curve = [[30, 40], [60, 50], [80, 90]]
        self.host.set_auto_curve(curve)
        self.assertEqual(
            self.host.raw_config["auto_curve"],
            [[30.0, 40], [60.0, 50], [80.0, 90]],
        )
        # 随后一次设置保存（使用 host.raw_config）不应丢掉刚编辑的曲线
        self.host.apply_raw(self.host.raw_config)
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["auto_curve"], [[30.0, 40], [60.0, 50], [80.0, 90]])


if __name__ == "__main__":
    unittest.main()
