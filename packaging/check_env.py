"""构建环境检查：在生成发布产物前硬性验证工具链。

用法：
    python packaging/check_env.py            # Linux/GUI 构建，要求能创建 Tk 窗口
    python packaging/check_env.py --skip-tk-window
"""

import argparse
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def fail(message: str) -> None:
    print(f"[FAIL] {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-tk-window", action="store_true")
    args = parser.parse_args()

    print(f"Python: {sys.version}")
    print(f"OS: {platform.platform()}")
    if sys.version_info < (3, 10):
        fail("需要 Python 3.10 或更高版本")

    source = str(ROOT)
    try:
        source.encode("ascii")
    except UnicodeEncodeError:
        fail(
            "源码路径包含非 ASCII 字符。PyInstaller/Tcl 在该路径下可能失败；"
            f"请复制到 ASCII 路径再构建：{source}"
        )

    try:
        import tkinter
    except ImportError:
        fail("缺少 tkinter。Linux 请安装 python3-tk；Windows 请使用官方 Python 安装包")
    print(f"Tk: {tkinter.TkVersion}")
    if not args.skip_tk_window:
        try:
            root = tkinter.Tk()
            root.withdraw()
            root.update_idletasks()
            root.destroy()
        except tkinter.TclError as exc:
            fail(
                f"无法创建 Tk 窗口：{exc}。请在图形会话或 xvfb-run 下运行构建检查"
            )

    try:
        import PyInstaller
    except ImportError:
        fail("缺少 PyInstaller，请 pip install -r requirements-build.txt")
    print(f"PyInstaller: {PyInstaller.__version__}")
    if not PyInstaller.__version__.startswith("6.18."):
        fail(f"PyInstaller 版本应为 6.18.x，当前为 {PyInstaller.__version__}")

    print("运行单元测试…")
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover"],
        cwd=ROOT,
        text=True,
    )
    if result.returncode != 0:
        fail("单元测试失败")

    print("[OK] 构建环境检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
