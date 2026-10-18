import logging
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Callable

from .. import __version__
from ..config import GpuMapping, normalize_pci_bus_id
from .host import ControllerHost
from .theme import COLORS, apply_theme
from .widgets import CurvePanel, GpuCard

log = logging.getLogger(__name__)


class QueueLogHandler(logging.Handler):
    def __init__(self, target: queue.Queue):
        super().__init__()
        self.target = target

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.target.put_nowait(self.format(record))
        except Exception:
            pass


def _asset_path(name: str) -> Path | None:
    candidates = [Path(__file__).resolve().parent.parent / "assets" / name]
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidates.insert(0, Path(bundle) / "gpu_fan_controller" / "assets" / name)
    for path in candidates:
        if path.exists():
            return path
    return None


class DesktopApp:
    def __init__(self, host: ControllerHost):
        self.host = host
        self.root = tk.Tk()
        self.root.title(f"Tesla T10 风扇控制器 V{__version__}")
        self.root.geometry("1280x900")
        self.root.minsize(1080, 720)
        self.theme = apply_theme(self.root)
        self.fonts = {key: self.theme[key] for key in ("small", "base", "bold", "title", "card_title", "temp")}
        self.fonts["family"] = self.theme["font"]
        icon = _asset_path("icon.png")
        if icon is not None:
            try:
                self._icon = tk.PhotoImage(file=str(icon))
                self.root.iconphoto(True, self._icon)
            except tk.TclError:
                self._icon = None

        self.log_queue: queue.Queue = queue.Queue()
        self._log_handler = QueueLogHandler(self.log_queue)
        self._log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger("gpu_fan_controller").addHandler(self._log_handler)
        logging.getLogger("gpu_fan_controller").setLevel(logging.INFO)

        self._closing = False
        self._cards: dict[int, GpuCard] = {}
        self._mapping_combos: dict[int, tuple[ttk.Combobox, tk.StringVar, dict]] = {}
        self._channel_options: list[dict] = []
        self._channel_label_to_data: dict[str, dict] = {}
        self._mapping_busy = False
        self._settings_busy = False
        self._refresh_timer = None
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.host.start()
        self._poll()

    # ---------------------------------------------------------------- 构建界面

    def _build(self) -> None:
        header = tk.Frame(self.root, bg=COLORS["panel"], highlightbackground=COLORS["border"], highlightthickness=0)
        header.pack(fill="x")
        header_inner = ttk.Frame(header, style="Header.TFrame", padding=(16, 10))
        header_inner.pack(fill="x")
        header_inner.columnconfigure(0, weight=1)
        title_wrap = ttk.Frame(header_inner, style="Header.TFrame")
        title_wrap.grid(row=0, column=0, sticky="w")
        ttk.Label(title_wrap, style="Title.TLabel", text="Tesla T10 风扇控制器").pack(anchor="w")
        ttk.Label(
            title_wrap,
            style="PanelMuted.TLabel",
            text=f"V{__version__} · 4× Tesla T10 · 原生桌面应用",
        ).pack(anchor="w")
        self.header_status = ttk.Label(header_inner, style="Status.TLabel", text="正在启动…")
        self.header_status.grid(row=0, column=1, sticky="e")

        self.mode_banner = tk.Frame(self.root, bg=COLORS["warn"])
        self.mode_banner.pack(fill="x")
        self.mode_label = tk.Label(
            self.mode_banner,
            text="",
            bg=COLORS["warn"],
            fg=COLORS["white"],
            anchor="w",
            padx=16,
            pady=6,
        )
        self.mode_label.pack(fill="x")

        self.error_banner = tk.Frame(self.root, bg=COLORS["band"])
        self.error_label = tk.Label(
            self.error_banner,
            text="",
            bg=COLORS["band"],
            fg="#b42318",
            anchor="w",
            justify="left",
            padx=16,
            pady=5,
        )
        self.error_label.pack(fill="x")

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=(8, 12))
        self.monitor_tab = ttk.Frame(self.notebook, padding=8)
        self.mapping_tab = ttk.Frame(self.notebook, padding=8)
        self.settings_tab = ttk.Frame(self.notebook, padding=8)
        self.log_tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(self.monitor_tab, text="监控")
        self.notebook.add(self.mapping_tab, text="风扇映射")
        self.notebook.add(self.settings_tab, text="设置")
        self.notebook.add(self.log_tab, text="日志")
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self._build_monitor()
        self._build_mapping()
        self._build_settings()
        self._build_log()

    def _build_monitor(self) -> None:
        self.curve_panel = CurvePanel(
            self.monitor_tab,
            self.fonts,
            on_apply_curve=self._apply_curve,
            on_reset=self._reset_curve,
        )
        self.curve_panel.pack(fill="x", pady=(0, 8))
        self.cards_frame = ttk.Frame(self.monitor_tab)
        self.cards_frame.pack(fill="both", expand=True)

    def _build_mapping(self) -> None:
        top = ttk.Frame(self.mapping_tab)
        top.pack(fill="x")
        top.columnconfigure(0, weight=1)
        ttk.Label(
            top,
            text="枚举主板上实际存在的风扇通道，并为每块 GPU 选择散热风扇。",
        ).grid(row=0, column=0, sticky="w")
        self.mapping_refresh_btn = ttk.Button(top, text="刷新通道 / GPU", command=self.refresh_mapping)
        self.mapping_refresh_btn.grid(row=0, column=1, sticky="e")
        self.mapping_status = ttk.Label(self.mapping_tab, style="Muted.TLabel", text="尚未读取")
        self.mapping_status.pack(fill="x", pady=(4, 6))

        columns = ("channel", "name", "rpm", "duty", "state", "gpu")
        self.channel_tree = ttk.Treeview(
            self.mapping_tab, columns=columns, show="headings", height=8
        )
        headings = {
            "channel": "通道",
            "name": "传感器",
            "rpm": "转速",
            "duty": "当前占空比",
            "state": "状态",
            "gpu": "当前映射",
        }
        widths = {"channel": 60, "name": 140, "rpm": 100, "duty": 100, "state": 120, "gpu": 120}
        for key in columns:
            self.channel_tree.heading(key, text=headings[key])
            self.channel_tree.column(key, width=widths[key], anchor="w", stretch=key in {"name", "state"})
        self.channel_tree.pack(fill="both", expand=True, pady=(0, 10))

        self.mapping_frame = ttk.LabelFrame(self.mapping_tab, text="GPU ↔ 风扇通道", padding=10)
        self.mapping_frame.pack(fill="x")
        self.mapping_rows = ttk.Frame(self.mapping_frame)
        self.mapping_rows.pack(fill="x")
        bottom = ttk.Frame(self.mapping_tab)
        bottom.pack(fill="x", pady=(8, 0))
        bottom.columnconfigure(0, weight=1)
        self.mapping_hint = ttk.Label(
            bottom,
            style="Muted.TLabel",
            text="无转速反馈的通道仍可使用，但会自动标记为“不校验 RPM”。",
        )
        self.mapping_hint.grid(row=0, column=0, sticky="w")
        self.mapping_apply_btn = ttk.Button(
            bottom, text="保存并应用映射", style="Accent.TButton", command=self._apply_mappings
        )
        self.mapping_apply_btn.grid(row=0, column=1, sticky="e")

    def _build_settings(self) -> None:
        wrap = ttk.Frame(self.settings_tab)
        wrap.pack(fill="both", expand=True)
        wrap.columnconfigure(0, weight=1)
        wrap.columnconfigure(1, weight=1)

        ipmi = ttk.LabelFrame(wrap, text="IPMI 连接", padding=10)
        ipmi.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        ipmi.columnconfigure(1, weight=1)
        self.var_transport = tk.StringVar()
        self.var_network_backend = tk.StringVar()
        self.var_ipmi_host = tk.StringVar()
        self.var_ipmi_port = tk.StringVar()
        self.var_ipmi_user = tk.StringVar()
        self.var_ipmi_password = tk.StringVar()
        self.var_ipmi_password_env = tk.StringVar()
        self.var_ipmi_interface = tk.StringVar()
        self.var_ipmi_privilege = tk.StringVar()
        self.var_ipmitool = tk.StringVar()
        self.var_nvidia_smi = tk.StringVar()

        self._combo_field(ipmi, 0, "传输方式", self.var_transport, ["auto", "local", "network"])
        self._combo_field(ipmi, 1, "网络后端", self.var_network_backend, ["auto", "pyghmi", "ipmitool"])
        self._entry_field(ipmi, 2, "BMC 地址", self.var_ipmi_host)
        self._entry_field(ipmi, 3, "BMC 端口", self.var_ipmi_port, width=10)
        self._entry_field(ipmi, 4, "用户名", self.var_ipmi_user)
        self._entry_field(ipmi, 5, "密码", self.var_ipmi_password, show="•")
        self._entry_field(ipmi, 6, "密码环境变量", self.var_ipmi_password_env)
        self._entry_field(ipmi, 7, "IPMI 接口", self.var_ipmi_interface)
        self._entry_field(ipmi, 8, "权限级别", self.var_ipmi_privilege)
        self._entry_field(ipmi, 9, "ipmitool 路径", self.var_ipmitool)
        self._entry_field(ipmi, 10, "nvidia-smi 路径", self.var_nvidia_smi)
        ttk.Label(
            ipmi,
            style="PanelMuted.TLabel",
            text="Windows 推荐使用 pyghmi 网络后端；密码保存到配置文件，注意文件权限。",
            wraplength=420,
            justify="left",
        ).grid(row=11, column=0, columnspan=2, sticky="w", pady=(6, 0))

        control = ttk.LabelFrame(wrap, text="风扇控制与保护", padding=10)
        control.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        control.columnconfigure(1, weight=1)
        self.var_control_enabled = tk.BooleanVar()
        self.var_restore_auto = tk.BooleanVar()
        self.var_log_level = tk.StringVar()
        self.var_poll = tk.StringVar()
        self.var_stale = tk.StringVar()
        self.var_override = tk.StringVar()
        self.var_release = tk.StringVar()
        self.var_min_duty = tk.StringVar()
        self.var_default_duty = tk.StringVar()
        ttk.Checkbutton(
            control, text="启用真实风扇控制（control_enabled）", variable=self.var_control_enabled
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self._entry_field(control, 1, "采样间隔（秒）", self.var_poll, width=10)
        self._entry_field(control, 2, "数据过期（秒）", self.var_stale, width=10)
        self._entry_field(control, 3, "热保护触发（°C）", self.var_override, width=10)
        self._entry_field(control, 4, "热保护解除（°C）", self.var_release, width=10)
        self._entry_field(control, 5, "手动下限（%）", self.var_min_duty, width=10)
        self._entry_field(control, 6, "手动默认（%）", self.var_default_duty, width=10)
        ttk.Checkbutton(
            control,
            text="正常停止时交还 BMC 自动（restore_auto_on_stop）",
            variable=self.var_restore_auto,
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self._combo_field(
            control, 8, "日志级别", self.var_log_level,
            ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        )
        ttk.Label(
            control,
            style="PanelMuted.TLabel",
            text="保存后控制器会重启：旧通道交还 BMC 自动控制，再按新配置接管。",
            wraplength=420,
            justify="left",
        ).grid(row=9, column=0, columnspan=2, sticky="w", pady=(6, 0))

        actions = ttk.Frame(self.settings_tab)
        actions.pack(fill="x", pady=(10, 0))
        actions.columnconfigure(0, weight=1)
        self.settings_status = ttk.Label(actions, style="Muted.TLabel", text="")
        self.settings_status.grid(row=0, column=0, sticky="w")
        self.settings_test_btn = ttk.Button(actions, text="测试连接", command=self._test_connection)
        self.settings_test_btn.grid(row=0, column=1, padx=(0, 8))
        self.settings_save_btn = ttk.Button(
            actions, text="保存并应用", style="Accent.TButton", command=self._save_settings
        )
        self.settings_save_btn.grid(row=0, column=2)
        self._load_settings_vars()

    def _entry_field(self, parent, row, label, variable, width=30, show=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3, padx=(0, 8))
        entry = ttk.Entry(parent, textvariable=variable, width=width, show=show)
        entry.grid(row=row, column=1, sticky="ew", pady=3)
        return entry

    def _combo_field(self, parent, row, label, variable, values):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3, padx=(0, 8))
        combo = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly")
        combo.grid(row=row, column=1, sticky="ew", pady=3)
        return combo

    def _build_log(self) -> None:
        top = ttk.Frame(self.log_tab)
        top.pack(fill="x")
        ttk.Label(top, text="运行日志（最近 500 行）").pack(side="left")
        ttk.Button(top, text="清空", command=self._clear_log).pack(side="right")
        self.log_text = tk.Text(
            self.log_tab,
            bg=COLORS["panel"],
            fg=COLORS["text"],
            relief="solid",
            borderwidth=1,
            font=(self.theme["font"], 9),
            wrap="none",
        )
        scroll = ttk.Scrollbar(self.log_tab, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set, state="disabled")
        self.log_text.pack(side="left", fill="both", expand=True, pady=(6, 0))
        scroll.pack(side="right", fill="y", pady=(6, 0))
        ttk.Label(
            self.log_tab, style="Muted.TLabel", text=f"配置文件：{self.host.config_path}"
        ).pack(fill="x", pady=(6, 0))

    # ---------------------------------------------------------------- 设置变量

    def _load_settings_vars(self) -> None:
        raw = self.host.raw_config
        self.var_transport.set(str(raw.get("ipmi_transport", "auto")))
        self.var_network_backend.set(str(raw.get("ipmi_network_backend", "auto")))
        self.var_ipmi_host.set(str(raw.get("ipmi_host", "")))
        self.var_ipmi_port.set(str(raw.get("ipmi_port", 623)))
        self.var_ipmi_user.set(str(raw.get("ipmi_username", "")))
        self.var_ipmi_password.set(str(raw.get("ipmi_password", "")))
        self.var_ipmi_password_env.set(str(raw.get("ipmi_password_env", "IPMI_PASSWORD")))
        self.var_ipmi_interface.set(str(raw.get("ipmi_interface", "lanplus")))
        self.var_ipmi_privilege.set(str(raw.get("ipmi_privilege", "ADMINISTRATOR")))
        self.var_ipmitool.set(str(raw.get("ipmitool_path", "ipmitool")))
        self.var_nvidia_smi.set(str(raw.get("nvidia_smi_path", "nvidia-smi")))
        self.var_control_enabled.set(bool(raw.get("control_enabled", False)))
        self.var_restore_auto.set(bool(raw.get("restore_auto_on_stop", True)))
        self.var_log_level.set(str(raw.get("log_level", "INFO")))
        self.var_poll.set(str(raw.get("poll_interval", 2.0)))
        self.var_stale.set(str(raw.get("stale_after", 10.0)))
        self.var_override.set(str(raw.get("thermal_override_temp", 85)))
        self.var_release.set(str(raw.get("thermal_release_temp", 78)))
        self.var_min_duty.set(str(raw.get("min_manual_duty", 20)))
        self.var_default_duty.set(str(raw.get("default_manual_duty", 60)))

    def _settings_raw(self) -> dict:
        raw = self.host.raw_config
        raw.update(
            {
                "ipmi_transport": self.var_transport.get(),
                "ipmi_network_backend": self.var_network_backend.get(),
                "ipmi_host": self.var_ipmi_host.get().strip(),
                "ipmi_port": int(self.var_ipmi_port.get()),
                "ipmi_username": self.var_ipmi_user.get().strip(),
                "ipmi_password": self.var_ipmi_password.get(),
                "ipmi_password_env": self.var_ipmi_password_env.get().strip(),
                "ipmi_interface": self.var_ipmi_interface.get().strip(),
                "ipmi_privilege": self.var_ipmi_privilege.get().strip(),
                "ipmitool_path": self.var_ipmitool.get().strip(),
                "nvidia_smi_path": self.var_nvidia_smi.get().strip(),
                "control_enabled": bool(self.var_control_enabled.get()),
                "restore_auto_on_stop": bool(self.var_restore_auto.get()),
                "log_level": self.var_log_level.get(),
                "poll_interval": float(self.var_poll.get()),
                "stale_after": float(self.var_stale.get()),
                "thermal_override_temp": float(self.var_override.get()),
                "thermal_release_temp": float(self.var_release.get()),
                "min_manual_duty": int(self.var_min_duty.get()),
                "default_manual_duty": int(self.var_default_duty.get()),
            }
        )
        return raw

    # ---------------------------------------------------------------- 轮询与刷新

    def _poll(self) -> None:
        if self._closing:
            return
        try:
            snapshot = self.host.snapshot()
            self._update_snapshot(snapshot)
        except Exception:
            log.exception("刷新界面失败")
        self._drain_log()
        self._refresh_timer = self.root.after(500, self._poll)

    def _update_snapshot(self, snapshot: dict) -> None:
        control_enabled = bool(snapshot.get("control_enabled"))
        dry_run = bool(snapshot.get("dry_run"))
        errors = snapshot.get("errors") or {}
        state = self._mode_state(control_enabled, dry_run)
        self.mode_label.configure(text=state[0], bg=state[1])
        self.mode_banner.configure(bg=state[1])
        transport = snapshot.get("ipmi_transport", "—")
        control_text = "已启用" if control_enabled else "只读"
        self.header_status.configure(
            text=f"{'演练模式' if dry_run else '实机模式'} · 控制 {control_text} · IPMI {transport}"
        )

        lines = []
        if errors.get("gpu"):
            lines.append("GPU 采样：" + errors["gpu"])
        if errors.get("ipmi"):
            lines.append("BMC/IPMI：" + errors["ipmi"])
        if errors.get("control"):
            lines.append("控制：" + errors["control"])
        if errors.get("mapping"):
            lines.append("映射：" + errors["mapping"])
        if snapshot.get("curve_save_error"):
            lines.append("曲线保存：" + str(snapshot["curve_save_error"]))
        access_warning = self.host.ipmi_access_warning
        if access_warning:
            lines.append("IPMI 权限：" + access_warning)
        if lines:
            self.error_label.configure(text="\n".join(lines))
            self.error_banner.pack(fill="x", after=self.mode_banner)
        else:
            self.error_banner.pack_forget()

        gpus = snapshot.get("gpus") or []
        gpu_ids = [gpu["id"] for gpu in gpus]
        if set(gpu_ids) != set(self._cards):
            for card in self._cards.values():
                card.destroy()
            self._cards.clear()
            columns = min(4, max(1, len(gpu_ids)))
            rows = max(1, (len(gpu_ids) + columns - 1) // columns)
            for column in range(4):
                self.cards_frame.columnconfigure(
                    column,
                    weight=1 if column < columns else 0,
                    uniform="card" if column < columns else "",
                )
            for row in range(4):
                self.cards_frame.rowconfigure(row, weight=1 if row < rows else 0)
            for index, gpu_id in enumerate(sorted(gpu_ids)):
                card = GpuCard(
                    self.cards_frame,
                    gpu_id,
                    self.fonts,
                    on_auto=self._set_auto,
                    on_manual=self._set_manual,
                    on_duty=self._set_duty,
                )
                card.grid(
                    row=index // columns,
                    column=index % columns,
                    sticky="nsew",
                    padx=5,
                    pady=5,
                )
                self._cards[gpu_id] = card
        limits = snapshot.get("limits") or {}
        min_duty = int(limits.get("min_manual_duty", 20))
        override = float(limits.get("thermal_override_temp", 85))
        for gpu in gpus:
            card = self._cards.get(gpu["id"])
            if card is not None:
                card.update_state(gpu, control_enabled, min_duty, override)
        self.curve_panel.update_state(snapshot)

    @staticmethod
    def _mode_state(control_enabled: bool, dry_run: bool):
        if dry_run:
            return (
                "演练模式（dry-run）：不会向主板写入真实命令，所有操作仅模拟。",
                COLORS["warn"],
            )
        if control_enabled:
            return (
                "实机模式：控制命令会真实下发到主板风扇。请确认 GPU 与风扇通道映射正确。",
                COLORS["danger"],
            )
        return (
            "只读模式：control_enabled=false，当前只监控、不写风扇。",
            COLORS["accent"],
        )

    def _drain_log(self) -> None:
        lines = []
        while len(lines) < 200:
            try:
                lines.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        if not lines:
            return
        self.log_text.configure(state="normal")
        self.log_text.insert("end", "\n".join(lines) + "\n")
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > 500:
            self.log_text.delete("1.0", f"{line_count - 500}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    # ---------------------------------------------------------------- 卡片/曲线动作

    def _set_auto(self, gpu_id: int) -> None:
        try:
            self.host.set_control(gpu_id, "auto")
        except (ValueError, KeyError) as exc:
            messagebox.showerror("切换自动失败", str(exc), parent=self.root)

    def _set_manual(self, gpu_id: int, duty: int) -> None:
        try:
            self.host.set_control(gpu_id, "manual", duty)
        except (ValueError, KeyError) as exc:
            messagebox.showerror("切换手动失败", str(exc), parent=self.root)

    def _set_duty(self, gpu_id: int, duty: int) -> None:
        try:
            self.host.set_control(gpu_id, "manual", duty)
        except (ValueError, KeyError) as exc:
            log.warning("设置 GPU%d 占空比失败：%s", gpu_id, exc)

    def _apply_curve(self, curve) -> None:
        try:
            self.host.set_auto_curve(curve)
            snapshot = self.host.snapshot()
            if snapshot.get("curve_save_error"):
                self.curve_panel.apply_result(
                    False, "已生效，但保存失败：" + str(snapshot["curve_save_error"])
                )
            else:
                self.curve_panel.apply_result(True, "曲线已保存")
            self._update_snapshot(snapshot)
        except Exception as exc:
            log.exception("保存曲线失败")
            self.curve_panel.apply_result(False, f"保存失败：{exc}")

    def _reset_curve(self) -> None:
        self.curve_panel.set_pending(True, "正在恢复默认曲线…")
        try:
            self.host.reset_auto_curve()
            snapshot = self.host.snapshot()
            if snapshot.get("curve_save_error"):
                self.curve_panel.apply_result(
                    False, "已恢复，但保存失败：" + str(snapshot["curve_save_error"])
                )
            else:
                self.curve_panel.apply_result(True, "已恢复默认曲线")
            self._update_snapshot(snapshot)
        except Exception as exc:
            log.exception("恢复默认曲线失败")
            self.curve_panel.apply_result(False, f"恢复失败：{exc}")

    # ---------------------------------------------------------------- 映射

    def _on_tab_changed(self, _event=None) -> None:
        if (
            self.notebook.select() == str(self.mapping_tab)
            and not self._mapping_combos
            and not self._mapping_busy
        ):
            self.refresh_mapping()

    def refresh_mapping(self) -> None:
        if self._mapping_busy:
            return
        self._mapping_busy = True
        self.mapping_refresh_btn.configure(state="disabled")
        self.mapping_status.configure(text="正在读取 GPU 与风扇通道…")
        self._run_async(self._collect_mapping_data, self._mapping_loaded, self._mapping_error)

    def _collect_mapping_data(self):
        return self.host.probe_gpus(), self.host.enumerate_fans()

    def _mapping_loaded(self, result) -> None:
        self._mapping_busy = False
        self.mapping_refresh_btn.configure(state="normal")
        gpus, fans = result
        channels = fans.get("channels", [])
        rpm_error = fans.get("rpm_error")
        if rpm_error:
            self.mapping_status.configure(
                text=f"已读取通道，但转速读取失败：{rpm_error}", foreground=COLORS["danger"]
            )
        else:
            with_rpm = sum(
                1 for item in channels
                if isinstance(item.get("rpm"), (int, float)) and item["rpm"] > 0
            )
            no_tach = sum(1 for item in channels if item.get("has_tach") is False)
            text = f"检测到 {len(gpus)} 块 GPU，{with_rpm} 个有转速的通道"
            if no_tach:
                text += f"，{no_tach} 个无转速反馈"
            self.mapping_status.configure(text=text, foreground=COLORS["muted"])
        self._render_channel_tree(channels)
        self._render_mapping_rows(gpus, channels)

    def _mapping_error(self, exc: Exception) -> None:
        self._mapping_busy = False
        self.mapping_refresh_btn.configure(state="normal")
        self.mapping_apply_btn.configure(state="normal")
        self.curve_panel.set_editable(True)
        self.mapping_status.configure(text=f"读取失败：{exc}", foreground=COLORS["danger"])
        log.error("读取风扇通道失败：%s", exc)

    def _render_channel_tree(self, channels: list[dict]) -> None:
        for item in self.channel_tree.get_children():
            self.channel_tree.delete(item)
        for channel in channels:
            if not channel.get("defined", channel.get("available")):
                continue
            if channel.get("present") is True and channel.get("has_tach") is not False:
                state = "CPU 风扇" if channel.get("role") == "cpu" else "有风扇"
            elif channel.get("has_tach") is False:
                state = "无转速反馈"
            elif channel.get("present") is None:
                state = "读数未知"
            else:
                state = "未检测到"
            mapped = (
                "GPU " + ", ".join(str(g) for g in channel.get("managed_by", []))
                if channel.get("managed_by")
                else "—"
            )
            rpm = channel.get("rpm")
            duty = channel.get("duty")
            duty_text = "BMC 自动" if channel.get("duty_mode") == "auto" else f"{duty}%"
            self.channel_tree.insert(
                "",
                "end",
                values=(
                    channel["channel"],
                    channel["name"],
                    f"{rpm:.0f} RPM" if isinstance(rpm, (int, float)) else "—",
                    duty_text,
                    state,
                    mapped,
                ),
            )

    def _render_mapping_rows(self, gpus: dict, channels: list[dict]) -> None:
        for child in self.mapping_rows.winfo_children():
            child.destroy()
        self._mapping_combos.clear()
        self._channel_options = []
        self._channel_label_to_data = {}
        for channel in channels:
            if not channel.get("defined", channel.get("available")):
                continue
            rpm = channel.get("rpm")
            rpm_text = f"{rpm:.0f} RPM" if isinstance(rpm, (int, float)) else "无转速"
            prefix = "CPU · " if channel.get("role") == "cpu" else ""
            label = f"通道 {channel['channel']} · {prefix}{channel['name']}（{rpm_text}）"
            option = dict(channel)
            option["label"] = label
            option["no_tach"] = channel.get("has_tach") is False
            self._channel_options.append(option)
            self._channel_label_to_data[label] = option

        existing = self.host.raw_config.get("mappings", [])
        by_pci = {normalize_pci_bus_id(str(item.get("pci_bus_id", ""))): item for item in existing}
        by_gpu = {int(item.get("gpu_id", -1)): item for item in existing}
        values = ["不接管"] + [option["label"] for option in self._channel_options]

        ttk.Label(self.mapping_rows, text="GPU", font=self.fonts["bold"]).grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 4)
        )
        ttk.Label(self.mapping_rows, text="散热风扇通道", font=self.fonts["bold"]).grid(
            row=0, column=1, sticky="w", pady=(0, 4)
        )
        self.mapping_rows.columnconfigure(1, weight=1)
        for row, (gpu_id, gpu) in enumerate(sorted(gpus.items()), start=1):
            pci = str(gpu.get("pci_bus_id", ""))
            ttk.Label(
                self.mapping_rows,
                text=f"GPU {gpu_id} · {gpu.get('name', '')}",
            ).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
            var = tk.StringVar(value="不接管")
            combo = ttk.Combobox(self.mapping_rows, textvariable=var, values=values, state="readonly")
            combo.grid(row=row, column=1, sticky="ew", pady=3)
            old = by_pci.get(normalize_pci_bus_id(pci)) or by_gpu.get(gpu_id)
            if old:
                old_channel = old.get("fan_channel")
                for option in self._channel_options:
                    if option["channel"] == old_channel:
                        var.set(option["label"])
                        break
            self._mapping_combos[gpu_id] = (combo, var, gpu)

    def _apply_mappings(self) -> None:
        if self._mapping_busy or self._settings_busy:
            return
        mappings = []
        used_channels = set()
        no_tach = []
        cpu_channels = []
        for gpu_id, (_combo, var, gpu) in sorted(self._mapping_combos.items()):
            label = var.get()
            if label == "不接管":
                continue
            option = self._channel_label_to_data.get(label)
            if option is None:
                messagebox.showerror("映射无效", f"GPU {gpu_id} 的风扇通道无效", parent=self.root)
                return
            channel = option["channel"]
            if channel in used_channels:
                messagebox.showerror(
                    "映射冲突",
                    f"通道 {channel} 被多块 GPU 选中，请为每块 GPU 选择不同通道。",
                    parent=self.root,
                )
                return
            used_channels.add(channel)
            if option.get("role") == "cpu":
                cpu_channels.append(f"GPU {gpu_id} → {option['name']}（通道 {channel}）")
            if option.get("no_tach"):
                no_tach.append(f"GPU {gpu_id} → {option['name']}（通道 {channel}）")
            mappings.append(
                GpuMapping(
                    gpu_id=gpu_id,
                    pci_bus_id=str(gpu.get("pci_bus_id", "")),
                    fan_name=str(option["name"]),
                    fan_channel=channel,
                    expect_rpm=not bool(option.get("no_tach")),
                )
            )
        if not mappings:
            messagebox.showerror("映射无效", "至少需要为一块 GPU 选择风扇通道。", parent=self.root)
            return
        if no_tach:
            if not messagebox.askyesno(
                "无转速反馈通道",
                "以下通道没有 RPM 读数，控制器将无法校验风扇是否真的转动：\n\n"
                + "\n".join(no_tach)
                + "\n\n仍要继续吗？",
                parent=self.root,
            ):
                return
        if cpu_channels:
            if not messagebox.askyesno(
                "CPU 风扇通道",
                "以下选择使用了通常给 CPU 散热的通道：\n\n"
                + "\n".join(cpu_channels)
                + "\n\n确认这些通道实际吹向 GPU 吗？",
                parent=self.root,
            ):
                return
        if not messagebox.askyesno(
            "应用映射",
            "应用后控制器会重启：旧风扇通道先交还 BMC 自动控制，再按新映射接管。\n\n继续吗？",
            parent=self.root,
        ):
            return
        self._mapping_busy = True
        self.mapping_apply_btn.configure(state="disabled")
        self.mapping_refresh_btn.configure(state="disabled")
        self.curve_panel.set_editable(False)
        self.mapping_status.configure(text="正在应用映射并重启控制器…", foreground=COLORS["muted"])

        def work():
            return self.host.apply_mappings(mappings)

        self._run_async(work, self._mapping_applied, self._mapping_error)

    def _mapping_applied(self, result) -> None:
        self._mapping_busy = False
        self.mapping_apply_btn.configure(state="normal")
        self.mapping_refresh_btn.configure(state="normal")
        self.curve_panel.set_editable(True)
        if result.get("save_error"):
            self.mapping_status.configure(
                text=f"映射已生效，但保存失败：{result['save_error']}", foreground=COLORS["danger"]
            )
        elif result.get("release_error"):
            self.mapping_status.configure(
                text=f"映射已生效，但旧通道交还 BMC 失败：{result['release_error']}",
                foreground=COLORS["danger"],
            )
        else:
            self.mapping_status.configure(
                text="映射已应用，控制器已按新映射重启。", foreground=COLORS["ok"]
            )
        self._load_settings_vars()
        self.refresh_mapping()
        self._update_snapshot(self.host.snapshot())

    # ---------------------------------------------------------------- 设置

    def _test_connection(self) -> None:
        if self._settings_busy:
            return
        try:
            raw = self._settings_raw()
        except ValueError as exc:
            messagebox.showerror("参数错误", f"端口或数值字段无效：{exc}", parent=self.root)
            return
        self._settings_busy = True
        self.settings_test_btn.configure(state="disabled")
        self.settings_status.configure(text="正在测试 IPMI 连接…", foreground=COLORS["muted"])

        def work():
            return self.host.candidate_report(raw)

        self._run_async(work, self._test_ok, self._test_error)

    def _test_ok(self, report) -> None:
        self._settings_busy = False
        self.settings_test_btn.configure(state="normal")
        duties = ", ".join(f"{value}%" for value in report["duties"])
        self.settings_status.configure(
            text=f"连接成功：{report['transport']} · 占空比 {duties} · 读到 {len(report['rpms'])} 路转速",
            foreground=COLORS["ok"],
        )

    def _test_error(self, exc: Exception) -> None:
        self._settings_busy = False
        self.settings_test_btn.configure(state="normal")
        self.settings_status.configure(text=f"连接失败：{exc}", foreground=COLORS["danger"])

    def _save_settings(self) -> None:
        if self._settings_busy:
            return
        try:
            raw = self._settings_raw()
        except ValueError as exc:
            messagebox.showerror("参数错误", f"端口或数值字段无效：{exc}", parent=self.root)
            return
        enabling = bool(raw.get("control_enabled")) and not self.host.config.control_enabled
        if enabling:
            if not messagebox.askyesno(
                "启用真实控制",
                "即将向主板下发真实风扇控制命令。\n\n请确认 GPU ↔ 风扇通道映射已经核对，继续吗？",
                parent=self.root,
            ):
                return
        self._settings_busy = True
        self.settings_save_btn.configure(state="disabled")
        self.settings_test_btn.configure(state="disabled")
        self.curve_panel.set_editable(False)
        self.settings_status.configure(text="正在应用配置并重启控制器…", foreground=COLORS["muted"])

        def work():
            return self.host.apply_raw(raw)

        self._run_async(work, self._settings_applied, self._settings_error)

    def _settings_applied(self, result) -> None:
        self._settings_busy = False
        self.settings_save_btn.configure(state="normal")
        self.settings_test_btn.configure(state="normal")
        self.curve_panel.set_editable(True)
        logging.getLogger().setLevel(
            getattr(logging, result["config"].log_level, logging.INFO)
        )
        if result.get("save_error"):
            self.settings_status.configure(
                text=f"配置已生效，但保存失败：{result['save_error']}", foreground=COLORS["danger"]
            )
        elif result.get("release_error"):
            self.settings_status.configure(
                text=f"配置已生效，但旧通道交还 BMC 失败：{result['release_error']}",
                foreground=COLORS["danger"],
            )
        else:
            self.settings_status.configure(text="配置已保存并应用。", foreground=COLORS["ok"])
        self._load_settings_vars()
        self._update_snapshot(self.host.snapshot())

    def _settings_error(self, exc: Exception) -> None:
        self._settings_busy = False
        self.settings_save_btn.configure(state="normal")
        self.settings_test_btn.configure(state="normal")
        self.curve_panel.set_editable(True)
        self.settings_status.configure(text=f"应用失败：{exc}", foreground=COLORS["danger"])
        messagebox.showerror("应用失败", str(exc), parent=self.root)

    # ---------------------------------------------------------------- 线程/关闭

    def _run_async(self, fn: Callable[[], Any], on_ok: Callable[[Any], None], on_error: Callable[[Exception], None]) -> None:
        def worker():
            try:
                result = fn()
            except Exception as exc:
                log.exception("后台任务失败")
                self._safe_after(lambda: on_error(exc))
                return
            self._safe_after(lambda: on_ok(result))

        threading.Thread(target=worker, daemon=True).start()

    def _safe_after(self, callback) -> None:
        if self._closing:
            return
        try:
            self.root.after(0, callback)
        except tk.TclError:
            pass

    def _on_close(self) -> None:
        if self._closing:
            return
        if self.host.config.control_enabled and not self.host.dry_run:
            if not messagebox.askyesno(
                "退出",
                "退出后受管风扇将交还 BMC 自动控制。\n\n确定关闭 Tesla T10 风扇控制器吗？",
                parent=self.root,
            ):
                return
        self._closing = True
        if self._refresh_timer is not None:
            try:
                self.root.after_cancel(self._refresh_timer)
            except tk.TclError:
                pass
        self.mode_label.configure(text="正在停止控制器并交还 BMC 自动控制…", bg=COLORS["muted"])
        self.root.update_idletasks()
        finished = threading.Event()

        def stop_worker():
            try:
                self.host.stop()
            except Exception:
                log.exception("停止控制器失败")
            finally:
                finished.set()

        threading.Thread(target=stop_worker, name="stop-controller", daemon=True).start()
        deadline = time.monotonic() + max(15.0, self.host.config.command_timeout * 4 + 5)
        while not finished.is_set() and time.monotonic() < deadline:
            try:
                self.root.update()
            except tk.TclError:
                break
            time.sleep(0.05)
        if not finished.is_set():
            log.error("停止控制器超时，窗口将关闭；风扇可能停留在最后占空比")
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self) -> None:
        self.root.mainloop()
