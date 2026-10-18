import os
import sys
import types
import unittest
from unittest import mock

from gpu_fan_controller.hardware import (
    AsrockRackIpmi,
    DryRunIpmi,
    HardwareError,
    NvidiaSmi,
    PyghmiIpmi,
    resolve_ipmi_transport,
)
from tests.util import make_config


class NvidiaSmiTests(unittest.TestCase):
    def test_parse_csv(self):
        output = (
            "0, Tesla T10, 00000000:01:00.0, 68\n"
            "1, Tesla T10, 00000000:41:00.0, [N/A]\n"
        )
        with mock.patch("gpu_fan_controller.hardware._run", return_value=output):
            readings = NvidiaSmi(2.0).read_gpus()
        self.assertEqual(readings[0]["temperature_c"], 68.0)
        self.assertIsNone(readings[1]["temperature_c"])
        self.assertEqual(readings[1]["pci_bus_id"], "00000000:41:00.0")

    def test_empty_output_raises(self):
        with mock.patch("gpu_fan_controller.hardware._run", return_value=""):
            with self.assertRaises(HardwareError):
                NvidiaSmi(2.0).read_gpus()


class AsrockRackIpmiTests(unittest.TestCase):
    def setUp(self):
        self.config = make_config()
        self.backend = AsrockRackIpmi(self.config)

    def test_read_duties(self):
        with mock.patch.object(self.backend, "_ipmi", return_value="23 00 23 23 2d 2d 2d 2d"):
            self.assertEqual(self.backend.read_duties(), [0x23, 0, 0x23, 0x23, 0x2D, 0x2D, 0x2D, 0x2D])

    def test_read_duties_invalid(self):
        with mock.patch.object(self.backend, "_ipmi", return_value="23 00"):
            with self.assertRaises(HardwareError):
                self.backend.read_duties()
        with mock.patch.object(self.backend, "_ipmi", return_value="zz"):
            with self.assertRaises(HardwareError):
                self.backend.read_duties()

    def test_write_only_managed_channels(self):
        with mock.patch.object(self.backend, "_ipmi") as ipmi:
            with self.assertRaises(HardwareError):
                self.backend.write_duties({3: 50})
            ipmi.assert_not_called()

    def test_write_reads_back_and_verifies(self):
        responses = [
            "23 00 23 23 2d 2d 2d 2d",  # 写入前读取
            "",                          # 写入
            "23 00 23 23 32 2d 2d 2d",  # 回读确认
        ]
        with mock.patch.object(self.backend, "_ipmi", side_effect=responses) as ipmi:
            confirmed = self.backend.write_duties({4: 50})
        self.assertEqual(confirmed[4], 50)
        write_call = ipmi.call_args_list[1]
        self.assertEqual(write_call.args[:3], ("raw", "0x3a", "0x01"))
        self.assertEqual(
            write_call.args[3:],
            ("0x23", "0x00", "0x23", "0x23", "0x32", "0x2d", "0x2d", "0x2d"),
        )

    def test_write_mismatch_raises(self):
        responses = ["23 00 23 23 2d 2d 2d 2d", "", "23 00 23 23 2d 2d 2d 2d"]
        with mock.patch.object(self.backend, "_ipmi", side_effect=responses):
            with self.assertRaises(HardwareError):
                self.backend.write_duties({4: 50})

    def test_release_control_writes_zero(self):
        responses = [
            "00 00 23 23 32 2d 2d 2d",
            "",
            "00 00 23 23 00 2d 2d 2d",
        ]
        with mock.patch.object(self.backend, "_ipmi", side_effect=responses) as ipmi:
            confirmed = self.backend.release_control({4})
        self.assertEqual(confirmed[4], 0)
        write_call = ipmi.call_args_list[1]
        self.assertEqual(
            write_call.args[3:],
            ("0x00", "0x00", "0x23", "0x23", "0x00", "0x2d", "0x2d", "0x2d"),
        )

    def test_read_rpms(self):
        output = (
            "CPU1_FAN1        | 1200.000     | RPM        | ok    | 300.000  | 1800.000\n"
            "FRNT_FAN1        | na           | RPM        | na    | na       | na\n"
            "UNRELATED        | 999.000      | RPM        | ok    | 300.000  | 1800.000\n"
        )
        with mock.patch.object(self.backend, "_ipmi", return_value=output):
            rpms = self.backend.read_rpms()
        self.assertEqual(rpms["CPU1_FAN1"], 1200.0)
        self.assertIsNone(rpms["FRNT_FAN1"])
        self.assertNotIn("UNRELATED", rpms)


class DryRunIpmiTests(unittest.TestCase):
    def setUp(self):
        self.config = make_config()
        self.backend = DryRunIpmi(self.config)

    def test_write_and_read(self):
        self.backend.write_duties({4: 55})
        self.assertEqual(self.backend.read_duties()[4], 55)

    def test_reject_unmanaged_channel(self):
        with self.assertRaises(HardwareError):
            self.backend.write_duties({0: 50})

    def test_reject_out_of_range(self):
        with self.assertRaises(HardwareError):
            self.backend.write_duties({4: 101})

    def test_release_control(self):
        self.backend.write_duties({4: 55})
        self.backend.release_control({4})
        self.assertEqual(self.backend.read_duties()[4], 0)

    def test_rpms_follow_duty(self):
        rpms = self.backend.read_rpms()
        self.assertGreater(rpms["FRNT_FAN1"], 0)
        self.assertIn("CPU1_FAN1", rpms)

    def test_enumeration_marks_no_tach_channel(self):
        result = self.backend.enumerate_fans()
        channels = {item["channel"]: item for item in result["channels"]}
        self.assertTrue(channels[4]["present"])
        self.assertEqual(channels[4]["has_tach"], True)
        self.assertEqual(channels[4]["duty_mode"], "manual")
        self.assertFalse(channels[1]["defined"])
        self.assertIsNone(channels[6]["rpm"])
        self.assertEqual(channels[6]["has_tach"], False)
        self.assertIn("无转速", channels[6]["presence_reason"])


SENSOR_OUTPUT = """CPU1_FAN1        | 1800.000   | RPM        | ok    | na        | na        | 100.000   | na        | na        | na
FRNT_FAN1        | 600.000    | RPM        | ok    | na        | na        | 100.000   | na        | na        | na
FRNT_FAN2        | 800.000    | RPM        | ok    | na        | na        | 100.000   | na        | na        | na
FRNT_FAN3        | na         | RPM        | na    | na        | na        | 100.000   | na        | na        | na
FRNT_FAN4        | 800.000    | RPM        | ok    | na        | na        | 100.000   | na        | na        | na
REAR_FAN1        | 800.000    | RPM        | ok    | na        | na        | 100.000   | na        | na        | na
REAR_FAN2        | 800.000    | RPM        | ok    | na        | na        | 100.000   | na        | na        | na
UNRELATED        | 999.000    | RPM        | ok    | na        | na        | 100.000   | na        | na        | na
"""


class TransportTests(unittest.TestCase):
    def test_resolve_transport(self):
        with mock.patch("gpu_fan_controller.hardware.IS_WINDOWS", False), mock.patch(
            "gpu_fan_controller.hardware.os.path.exists", return_value=True
        ):
            self.assertEqual(
                resolve_ipmi_transport(make_config(ipmi_transport="auto")), "local"
            )
        with mock.patch("gpu_fan_controller.hardware.IS_WINDOWS", False), mock.patch(
            "gpu_fan_controller.hardware.os.path.exists", return_value=False
        ):
            self.assertEqual(
                resolve_ipmi_transport(make_config(ipmi_transport="auto", ipmi_host="10.0.0.2")),
                "network",
            )
            self.assertEqual(
                resolve_ipmi_transport(make_config(ipmi_transport="auto")), "local"
            )
        with mock.patch("gpu_fan_controller.hardware.IS_WINDOWS", True):
            self.assertEqual(
                resolve_ipmi_transport(make_config(ipmi_transport="auto")), "network"
            )

    def test_network_command_keeps_password_out_of_argv(self):
        config = make_config(
            ipmi_transport="network",
            ipmi_host="10.0.0.2",
            ipmi_username="admin",
            ipmi_password="s3cret",
        )
        backend = AsrockRackIpmi(config, transport="network")
        with mock.patch(
            "gpu_fan_controller.hardware._run",
            return_value="00 00 23 23 2d 2d 2d 2d",
        ) as run:
            backend.read_duties()
        command = run.call_args.args[0]
        self.assertIn("-I", command)
        self.assertIn("lanplus", command)
        self.assertIn("-H", command)
        self.assertIn("10.0.0.2", command)
        self.assertIn("-E", command)
        self.assertNotIn("s3cret", command)
        self.assertEqual(run.call_args.kwargs["env"]["IPMI_PASSWORD"], "s3cret")

    def test_network_without_password_raises(self):
        config = make_config(
            ipmi_transport="network",
            ipmi_host="10.0.0.2",
            ipmi_username="admin",
            ipmi_password="",
            ipmi_password_env="GPU_FAN_TEST_MISSING_PASSWORD",
        )
        with mock.patch.dict(os.environ, {"PATH": os.environ.get("PATH", "")}, clear=True):
            backend = AsrockRackIpmi(config, transport="network")
            with self.assertRaises(HardwareError) as ctx:
                backend.read_duties()
        self.assertIn("密码", str(ctx.exception))

    def test_enumeration_uses_sensor_status(self):
        config = make_config()
        backend = AsrockRackIpmi(config)

        def fake_ipmi(*args):
            if args[:3] == ("raw", "0x3a", "0x02"):
                return "00 00 23 23 2d 2d 2d 2d"
            if args == ("sensor",):
                return SENSOR_OUTPUT
            raise AssertionError(args)

        with mock.patch.object(backend, "_ipmi", side_effect=fake_ipmi):
            result = backend.enumerate_fans()
        channels = {item["channel"]: item for item in result["channels"]}
        self.assertIsNone(result["rpm_error"])
        self.assertTrue(channels[0]["present"])
        self.assertTrue(channels[0]["has_tach"])
        self.assertFalse(channels[1]["defined"])
        self.assertFalse(channels[1]["present"])
        self.assertEqual(channels[2]["duty"], 35)
        self.assertEqual(channels[2]["duty_mode"], "manual")
        self.assertEqual(channels[0]["duty_mode"], "auto")
        self.assertEqual(channels[6]["rpm_status"], "na")
        self.assertIsNone(channels[6]["rpm"])
        self.assertTrue(channels[6]["present"])
        self.assertFalse(channels[6]["has_tach"])
        self.assertIn(0, channels[4]["managed_by"])
        self.assertNotIn("UNRELATED", [item["name"] for item in result["channels"]])


class FakePyghmi:
    def __init__(self, duties):
        self.duties = list(duties)

    def module(self):
        pyghmi = types.ModuleType("pyghmi")
        ipmi = types.ModuleType("pyghmi.ipmi")
        command = types.ModuleType("pyghmi.ipmi.command")
        fake = self

        class FakeCommand:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.command = fake

            def raw_command(self, netfn, command, data=()):
                if (netfn, command) == (0x3A, 0x02):
                    return {"data": list(self.command.duties)}
                if (netfn, command) == (0x3A, 0x01):
                    self.command.duties = list(data)
                    return {"data": []}
                return {"data": []}

            def get_sensor_reading(self, name):
                if name == "FRNT_FAN3":
                    return types.SimpleNamespace(value=None, unavailable=1, units="RPM")
                return types.SimpleNamespace(value=1234.0, unavailable=0, units="RPM")

        command.Command = FakeCommand
        pyghmi.ipmi = ipmi
        ipmi.command = command
        return {
            "pyghmi": pyghmi,
            "pyghmi.ipmi": ipmi,
            "pyghmi.ipmi.command": command,
        }


class PyghmiTests(unittest.TestCase):
    def test_read_write_and_sensors(self):
        config = make_config(
            ipmi_transport="network",
            ipmi_host="10.0.0.2",
            ipmi_username="admin",
            ipmi_password="s3cret",
            ipmi_privilege="ADMINISTRATOR",
        )
        fake = FakePyghmi([0, 0, 35, 35, 45, 45, 45, 45])
        with mock.patch.dict(sys.modules, fake.module()):
            backend = PyghmiIpmi(config)
            self.assertEqual(backend.read_duties(), [0, 0, 35, 35, 45, 45, 45, 45])
            rpms = backend.read_rpms()
            self.assertEqual(rpms["FRNT_FAN1"], 1234.0)
            self.assertIsNone(rpms["FRNT_FAN3"])
            backend.write_duties({4: 50})
            self.assertEqual(backend.read_duties()[4], 50)
            self.assertEqual(backend._ipmi.kwargs["privlevel"], 4)
            self.assertEqual(backend._ipmi.kwargs["password"], "s3cret")

    def test_enumeration(self):
        config = make_config(
            ipmi_transport="network",
            ipmi_host="10.0.0.2",
            ipmi_username="admin",
            ipmi_password="s3cret",
        )
        fake = FakePyghmi([0, 0, 35, 35, 45, 45, 45, 45])
        with mock.patch.dict(sys.modules, fake.module()):
            result = PyghmiIpmi(config).enumerate_fans()
        channels = {item["channel"]: item for item in result["channels"]}
        self.assertTrue(channels[4]["present"])
        self.assertFalse(channels[6]["has_tach"])
        self.assertEqual(channels[6]["rpm_status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
