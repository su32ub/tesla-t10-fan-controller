import copy
import json
import tempfile
import unittest
from pathlib import Path

from gpu_fan_controller.config import (
    load_config,
    normalize_pci_bus_id,
    save_auto_curve,
    save_config,
    validate_curve,
)
from tests.util import config_dict

BASE = {
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
    "ipmitool_path": "ipmitool",
    "ipmi_channel_names": [
        "CPU1_FAN1",
        None,
        "REAR_FAN1",
        "REAR_FAN2",
        "FRNT_FAN1",
        "FRNT_FAN2",
        "FRNT_FAN3",
        "FRNT_FAN4",
    ],
    "auto_curve": [[0, 30], [60, 40], [70, 55], [75, 70], [80, 85], [85, 100], [100, 100]],
    "mappings": [
        {"gpu_id": 0, "pci_bus_id": "00000000:01:00.0", "fan_name": "FRNT_FAN1", "fan_channel": 4},
        {"gpu_id": 1, "pci_bus_id": "00000000:41:00.0", "fan_name": "FRNT_FAN2", "fan_channel": 5},
        {"gpu_id": 2, "pci_bus_id": "00000000:81:00.0", "fan_name": "FRNT_FAN4", "fan_channel": 7},
        {"gpu_id": 3, "pci_bus_id": "00000000:82:00.0", "fan_name": "REAR_FAN1", "fan_channel": 2},
    ],
}


class LoadConfigTests(unittest.TestCase):
    def load(self, **changes):
        raw = copy.deepcopy(BASE)
        raw.update(changes)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            return load_config(str(path))

    def assert_invalid(self, **changes):
        with self.assertRaises(ValueError):
            self.load(**changes)

    def test_valid_config(self):
        config = self.load()
        self.assertEqual(config.port, 8080)
        self.assertEqual(len(config.mappings), 4)
        self.assertEqual(config.log_level, "INFO")
        self.assertFalse(config.control_enabled)

    def test_log_level_normalized(self):
        self.assertEqual(self.load(log_level="debug").log_level, "DEBUG")
        self.assert_invalid(log_level="LOUD")

    def test_duplicate_gpu_or_channel(self):
        mappings = copy.deepcopy(BASE["mappings"])
        mappings[1]["gpu_id"] = 0
        self.assert_invalid(mappings=mappings)
        mappings = copy.deepcopy(BASE["mappings"])
        mappings[1]["fan_channel"] = 4
        self.assert_invalid(mappings=mappings)

    def test_duplicate_pci_bus_normalized(self):
        mappings = copy.deepcopy(BASE["mappings"])
        mappings[1]["pci_bus_id"] = "0000:01:00.0"
        self.assert_invalid(mappings=mappings)

    def test_fan_name_must_match_channel(self):
        mappings = copy.deepcopy(BASE["mappings"])
        mappings[0]["fan_name"] = "FRNT_FAN2"
        self.assert_invalid(mappings=mappings)

    def test_channel_range(self):
        mappings = copy.deepcopy(BASE["mappings"])
        mappings[0]["fan_channel"] = 8
        self.assert_invalid(mappings=mappings)

    def test_curve_must_increase(self):
        self.assert_invalid(auto_curve=[[0, 30], [60, 40], [60, 50]])
        self.assert_invalid(auto_curve=[[0, 30], [60, 101]])

    def test_thresholds(self):
        self.assert_invalid(thermal_release_temp=85)
        self.assert_invalid(stale_after=2.0)
        self.assert_invalid(poll_interval=0.5)

    def test_duty_bounds(self):
        self.assert_invalid(min_manual_duty=10)
        self.assert_invalid(default_manual_duty=19)

    def test_channel_names_length(self):
        self.assert_invalid(ipmi_channel_names=["A"] * 7)


class NormalizePciBusIdTests(unittest.TestCase):
    def test_forms(self):
        self.assertEqual(normalize_pci_bus_id("00000000:01:00.0"), "0000:01:00.0")
        self.assertEqual(normalize_pci_bus_id("0000:01:00.0"), "0000:01:00.0")
        self.assertEqual(normalize_pci_bus_id("01:00.0"), "0000:01:00.0")
        self.assertEqual(normalize_pci_bus_id(" 00000000:01:00.0 "), "0000:01:00.0")

    def test_invalid_passthrough(self):
        self.assertEqual(normalize_pci_bus_id("N/A"), "n/a")


class CurveTests(unittest.TestCase):
    def test_validate_curve(self):
        curve = validate_curve([[30, 35], [40, 38.4], [80, 90]])
        self.assertEqual(curve, ((30.0, 35), (40.0, 38), (80, 90)))

    def test_invalid_curves(self):
        invalid = [
            [[30, 35]],
            [[30, 35], [30, 40]],
            [[30, 35], [20, 40]],
            [[30, -1], [60, 40]],
            [[30, 101], [60, 40]],
            [[30, "35"], [60, 40]],
            [[30, True], [60, 40]],
            [[30, 35, 1], [60, 40]],
            "not a curve",
        ]
        for curve in invalid:
            with self.subTest(curve=curve):
                with self.assertRaises(ValueError):
                    validate_curve(curve)

    def test_save_auto_curve(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(config_dict()), encoding="utf-8")
            save_auto_curve(str(path), [[30, 45], [60, 60], [80, 95]])
            reloaded = load_config(str(path))
            self.assertEqual(
                reloaded.auto_curve, ((30.0, 45), (60.0, 60), (80.0, 95))
            )
            # 其他配置保持不变
            self.assertEqual(len(reloaded.mappings), 4)
            self.assertTrue(reloaded.control_enabled is False)
            self.assertFalse(list(Path(tmp).glob("*.tmp")))

    def test_save_invalid_curve_keeps_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            original = json.dumps(config_dict())
            path.write_text(original, encoding="utf-8")
            with self.assertRaises(ValueError):
                save_auto_curve(str(path), [[30, 40], [20, 50]])
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_v1_config_migrates_with_backup(self):
        raw = config_dict()
        raw.pop("config_version")
        raw.pop("ipmi_transport")
        raw.pop("ipmi_network_backend")
        raw["control_enabled"] = "false"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            config = load_config(str(path))
            self.assertFalse(config.control_enabled)
            self.assertEqual(config.ipmi_transport, "auto")
            self.assertEqual(config.config_version, 1)
            raw["config_version"] = 2
            save_config(str(path), raw)
            backup = Path(tmp) / "config.json.v1.bak"
            self.assertTrue(backup.exists())
            self.assertEqual(load_config(str(path)).config_version, 2)

    def test_bool_strings_are_parsed_strictly(self):
        for text, expected in (("false", False), ("0", False), ("true", True), ("yes", True)):
            raw = config_dict()
            raw["control_enabled"] = text
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "config.json"
                path.write_text(json.dumps(raw), encoding="utf-8")
                self.assertEqual(load_config(str(path)).control_enabled, expected)

    def test_duplicate_channel_names_rejected(self):
        raw = config_dict()
        raw["ipmi_channel_names"][1] = "CPU1_FAN1"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(str(path))

    def test_mapping_expect_rpm_false(self):
        raw = config_dict()
        raw["mappings"][0]["expect_rpm"] = False
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            config = load_config(str(path))
            self.assertFalse(config.mappings[0].expect_rpm)
            self.assertTrue(config.mappings[1].expect_rpm)


if __name__ == "__main__":
    unittest.main()
