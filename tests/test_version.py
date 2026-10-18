import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

from gpu_fan_controller import __version__
from gpu_fan_controller.web import FanRequestHandler


class VersionTests(unittest.TestCase):
    def test_version_is_consistent(self):
        root = Path(__file__).resolve().parents[1]
        metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(__version__, "2.0.0")
        self.assertEqual(metadata["project"]["version"], __version__)
        self.assertEqual(FanRequestHandler.server_version, f"GpuFanController/{__version__}")

    def test_cli_import_does_not_load_tkinter(self):
        code = (
            "import sys; import gpu_fan_controller.__main__; "
            "print('tkinter' in sys.modules)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).resolve().parents[1],
        )
        self.assertEqual(result.stdout.strip(), "False")


if __name__ == "__main__":
    unittest.main()
