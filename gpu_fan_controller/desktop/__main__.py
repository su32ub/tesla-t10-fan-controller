import argparse
import ctypes
import logging
import os
import subprocess
import sys
import tkinter as tk
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import messagebox

from .. import __version__
from ..config import default_raw_config, load_config, save_config
from ..instance_lock import InstanceLock
from .app import DesktopApp
from .host import ControllerHost, HostError

log = logging.getLogger(__name__)


def resolve_config_path(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env_path = os.environ.get("GPU_FAN_CONTROLLER_CONFIG")
    if env_path:
        return Path(env_path).expanduser().resolve()
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "config.json")
    project_root = Path(__file__).resolve().parents[2]
    candidates.append(project_root / "config.json")
    candidates.append(Path.cwd() / "config.json")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "GpuFanController"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "gpu-fan-controller"
    base.mkdir(parents=True, exist_ok=True)
    path = base / "config.json"
    if not path.exists():
        save_config(str(path), default_raw_config())
    return path


def resolve_state_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        path = base / "GpuFanController" / "logs"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
        path = base / "gpu-fan-controller"
    path.mkdir(parents=True, exist_ok=True)
    return path


def configure_logging(config) -> Path:
    logfile = resolve_state_dir() / "app.log"
    root = logging.getLogger()
    root.setLevel(getattr(logging, config.log_level, logging.INFO))
    if not any(isinstance(handler, RotatingFileHandler) for handler in root.handlers):
        handler = RotatingFileHandler(
            logfile, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(handler)
    if sys.stderr is not None and not any(
        isinstance(handler, logging.StreamHandler)
        and not isinstance(handler, RotatingFileHandler)
        for handler in root.handlers
    ):
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(stream)
    return logfile


def _enable_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _show_startup_error(message: str) -> None:
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("GPU 风扇控制器", message, parent=root)
        root.destroy()
    except Exception:
        if sys.stderr is not None:
            print(message, file=sys.stderr)


def _systemd_service_active() -> bool:
    if sys.platform != "linux":
        return False
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", "gpu-fan-controller"],
            timeout=2,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="gpu-fan-controller-desktop")
    parser.add_argument("--config", help="配置文件路径")
    parser.add_argument("--dry-run", action="store_true", help="演练模式，不写真实风扇")
    parser.add_argument(
        "--allow-concurrent",
        action="store_true",
        help="允许与已有控制器实例同时运行（危险，仅用于调试）",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="启动后 1.5 秒自动关闭，用于打包产物冒烟测试（配合 --dry-run）",
    )
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    if args.allow_concurrent and not args.dry_run:
        _show_startup_error(
            "--allow-concurrent 只能与 --dry-run 一起使用。\n\n"
            "正式控制时不允许两个控制器同时写同一组风扇。"
        )
        return 2

    _enable_dpi_awareness()
    config_path = resolve_config_path(args.config)
    try:
        config = load_config(str(config_path))
    except Exception as exc:
        _show_startup_error(f"无法加载配置 {config_path}：\n{exc}")
        return 1
    logfile = configure_logging(config)
    log.info("启动桌面程序：%s（配置 %s，日志 %s）", __version__, config_path, logfile)

    lock = InstanceLock()
    if not lock.acquire():
        message = (
            "检测到另一个 GPU 风扇控制器实例正在运行。\n\n"
            "请先关闭桌面程序，或停止 systemd 服务：\n"
            "  sudo systemctl stop gpu-fan-controller"
        )
        log.error(message)
        _show_startup_error(message)
        return 2

    host = None
    try:
        host = ControllerHost(config_path, dry_run=args.dry_run)
        if not args.allow_concurrent and _systemd_service_active():
            message = (
                "检测到 systemd 服务 gpu-fan-controller 正在运行。\n\n"
                "桌面程序和服务会同时改写同一组 IPMI 占空比字节。请先停止服务：\n"
                "  sudo systemctl stop gpu-fan-controller\n\n"
                "（如需迁移到桌面程序，可执行 sudo systemctl disable gpu-fan-controller）"
            )
            log.error(message)
            _show_startup_error(message)
            return 2
        if not args.allow_concurrent and host.web_service_running():
            message = (
                "检测到 systemd / 网页版控制器正在运行（端口 "
                f"{host.config.port}）。\n\n"
                "两个控制器同时接管风扇会互相覆盖。请先停止服务：\n"
                "  sudo systemctl stop gpu-fan-controller\n\n"
                "确认没有其他实例后重新启动桌面程序。"
            )
            log.error(message)
            _show_startup_error(message)
            return 2
        app = DesktopApp(host)
        if args.smoke_test:
            app.root.after(1500, app._on_close)
        app.run()
        return 0
    except HostError as exc:
        _show_startup_error(f"初始化失败：\n{exc}")
        return 1
    except Exception as exc:
        log.exception("桌面程序异常退出")
        _show_startup_error(f"程序异常退出：\n{exc}")
        return 1
    finally:
        if host is not None:
            try:
                host.stop()
            except Exception:
                log.exception("退出时停止控制器失败")
        lock.release()
        log.info("桌面程序已退出")


if __name__ == "__main__":
    raise SystemExit(main())
