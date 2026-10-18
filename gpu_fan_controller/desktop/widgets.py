import tkinter as tk
from tkinter import ttk
from typing import Callable

from .theme import COLORS

CURVE_X_MIN = 30
CURVE_X_MAX = 100
HANDLE_TEMPS = (30, 40, 50, 60, 70, 80)


def interpolate_curve(temp: float, curve) -> int:
    if temp <= curve[0][0]:
        return int(curve[0][1])
    if temp >= curve[-1][0]:
        return int(curve[-1][1])
    for index in range(1, len(curve)):
        left_temp, left_duty = curve[index - 1]
        right_temp, right_duty = curve[index]
        if temp <= right_temp:
            ratio = (temp - left_temp) / (right_temp - left_temp)
            return int(round(left_duty + ratio * (right_duty - left_duty)))
    return int(curve[-1][1])


def set_curve_duty(curve: list[list[float]], temp: float, duty: int) -> None:
    for point in curve:
        if abs(float(point[0]) - temp) < 1e-6:
            point[1] = duty
            return
    curve.append([temp, duty])
    curve.sort(key=lambda point: float(point[0]))


class GpuCard(tk.Frame):
    """单块 GPU 的监控卡片，包含自动/手动与占空比滑块。"""

    def __init__(
        self,
        parent,
        gpu_id: int,
        fonts: dict,
        on_auto: Callable[[int], None],
        on_manual: Callable[[int, int], None],
        on_duty: Callable[[int, int], None],
    ):
        super().__init__(
            parent,
            bg=COLORS["panel"],
            highlightbackground=COLORS["border"],
            highlightthickness=1,
            bd=0,
        )
        self.gpu_id = gpu_id
        self.fonts = fonts
        self.on_auto = on_auto
        self.on_manual = on_manual
        self.on_duty = on_duty
        self._suppress = False
        self._dragging = False
        self._debounce = None
        self._control_enabled = False
        self._min_duty = 20

        self.accent = tk.Frame(self, bg=COLORS["ok"], height=3)
        self.accent.pack(fill="x")

        body = ttk.Frame(self, style="Card.TFrame", padding=(10, 8))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)

        header = ttk.Frame(body, style="Card.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        title_wrap = ttk.Frame(header, style="Card.TFrame")
        title_wrap.grid(row=0, column=0, sticky="w")
        self.title_label = ttk.Label(title_wrap, style="CardTitle.TLabel", text=f"GPU {gpu_id}")
        self.title_label.pack(anchor="w")
        self.pci_label = ttk.Label(title_wrap, style="CardMuted.TLabel", text="PCI —")
        self.pci_label.pack(anchor="w")
        self.badge = ttk.Label(header, style="Badge.TLabel", text="")
        self.badge.grid(row=0, column=1, sticky="ne")

        temp_row = ttk.Frame(body, style="Card.TFrame")
        temp_row.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        temp_row.columnconfigure(1, weight=1)
        self.temp_label = ttk.Label(temp_row, style="Temp.TLabel", text="— °C")
        self.temp_label.grid(row=0, column=0, sticky="w")
        self.temp_age = ttk.Label(temp_row, style="CardMuted.TLabel", text="")
        self.temp_age.grid(row=0, column=1, sticky="e")

        metrics = ttk.Frame(body, style="Card.TFrame")
        metrics.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        for column in range(2):
            metrics.columnconfigure(column, weight=1)
        self.fan_label = ttk.Label(metrics, style="CardMuted.TLabel", text="风扇 —")
        self.fan_label.grid(row=0, column=0, columnspan=2, sticky="w")
        self.rpm_label = self._metric(metrics, 1, 0, "转速")
        self.target_label = self._metric(metrics, 1, 1, "目标占空比")
        self.actual_label = self._metric(metrics, 2, 0, "实际占空比")
        self.mode_label = self._metric(metrics, 2, 1, "模式")

        mode_row = ttk.Frame(body, style="Card.TFrame")
        mode_row.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.auto_btn = ttk.Button(
            mode_row, text="自动", width=8, command=lambda: self.on_auto(self.gpu_id)
        )
        self.auto_btn.pack(side="left")
        self.manual_btn = ttk.Button(
            mode_row, text="手动", width=8, command=self._manual_clicked
        )
        self.manual_btn.pack(side="left", padx=(6, 0))
        self.mode_hint = ttk.Label(mode_row, style="CardMuted.TLabel", text="")
        self.mode_hint.pack(side="left", padx=(8, 0))

        slider_row = ttk.Frame(body, style="Card.TFrame")
        slider_row.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        slider_row.columnconfigure(0, weight=1)
        self.duty_var = tk.DoubleVar(value=60)
        self.scale = ttk.Scale(
            slider_row,
            from_=20,
            to=100,
            variable=self.duty_var,
            command=self._scale_changed,
        )
        self.scale.grid(row=0, column=0, sticky="ew")
        self.duty_output = ttk.Label(
            slider_row, style="Card.TLabel", text="60%", width=5, anchor="e"
        )
        self.duty_output.grid(row=0, column=1, padx=(8, 0))
        self.scale.bind("<ButtonPress-1>", self._scale_press)
        self.scale.bind("<ButtonRelease-1>", self._scale_release)

        self.alarm_label = ttk.Label(
            body,
            style="CardMuted.TLabel",
            text="",
            wraplength=280,
            justify="left",
        )
        self.alarm_label.grid(row=5, column=0, sticky="ew", pady=(4, 0))

    def _metric(self, parent, row: int, column: int, label: str) -> ttk.Label:
        frame = ttk.Frame(parent, style="Card.TFrame")
        frame.grid(row=row, column=column, sticky="w", pady=(4, 0))
        ttk.Label(frame, style="CardMuted.TLabel", text=label).pack(anchor="w")
        value = ttk.Label(frame, style="Card.TLabel", text="—")
        value.pack(anchor="w")
        return value

    # ---------------------------------------------------------------- 事件

    def _manual_clicked(self) -> None:
        duty = int(round(self.duty_var.get()))
        self.on_manual(self.gpu_id, duty)

    def _scale_press(self, _event=None) -> None:
        self._dragging = True
        if self._debounce is not None:
            self.after_cancel(self._debounce)
            self._debounce = None

    def _scale_release(self, _event=None) -> None:
        self._dragging = False
        self._commit_duty()

    def _scale_changed(self, value) -> None:
        if self._suppress:
            return
        duty = int(round(float(value)))
        self.duty_output.configure(text=f"{duty}%")
        if self._debounce is not None:
            self.after_cancel(self._debounce)
        self._debounce = self.after(180, self._commit_duty)

    def _commit_duty(self) -> None:
        self._debounce = None
        if not self._control_enabled:
            return
        duty = max(self._min_duty, min(100, int(round(self.duty_var.get()))))
        self.duty_output.configure(text=f"{duty}%")
        self.on_duty(self.gpu_id, duty)

    # ---------------------------------------------------------------- 刷新

    def update_state(
        self,
        state: dict,
        control_enabled: bool,
        min_duty: int,
        override_temp: float = 85.0,
    ) -> None:
        self._control_enabled = control_enabled
        if min_duty != self._min_duty:
            self._min_duty = min_duty
            self.scale.configure(from_=min_duty)

        self.title_label.configure(text=f"GPU {state['id']} · {state.get('name') or '未知型号'}")
        self.pci_label.configure(text=f"PCI {state.get('pci_bus_id') or '—'}")
        temp = state.get("temperature_c")
        override = bool(state.get("thermal_override"))
        stale = bool(state.get("sensor_stale"))
        crit = override_temp
        high = max(60, crit - 10)

        if override:
            badge_text, badge_color, accent = "热保护", COLORS["protect"], COLORS["protect"]
        elif stale:
            badge_text, badge_color, accent = "传感器过期", COLORS["stale"], COLORS["stale"]
        elif isinstance(temp, (int, float)) and temp >= crit:
            badge_text, badge_color, accent = "过热", COLORS["danger"], COLORS["danger"]
        elif isinstance(temp, (int, float)) and temp >= high:
            badge_text, badge_color, accent = "高温", COLORS["warn"], COLORS["warn"]
        else:
            badge_text, badge_color, accent = "正常", COLORS["ok"], COLORS["ok"]
        self.badge.configure(text=badge_text, foreground=badge_color)
        self.accent.configure(bg=accent)

        if isinstance(temp, (int, float)):
            self.temp_label.configure(text=f"{temp:.1f} °C", foreground=badge_color if badge_text != "正常" else COLORS["text"])
            age = state.get("temperature_age_seconds")
            self.temp_age.configure(text=f"数据 {age:.0f}s 前" if isinstance(age, (int, float)) and age >= 2 else "")
        else:
            self.temp_label.configure(text="— °C", foreground=COLORS["muted"])
            self.temp_age.configure(text="")

        self.fan_label.configure(
            text=f"风扇 {state.get('fan_name') or '—'}（通道 {state.get('fan_channel')}）"
        )
        rpm = state.get("fan_rpm")
        if rpm is None:
            rpm_text = "—" if state.get("expect_rpm", True) else "无转速反馈"
        else:
            rpm_text = f"{rpm:.0f} RPM"
        self.rpm_label.configure(text=rpm_text)
        self.target_label.configure(text=self._duty_text(state.get("requested_duty")))
        actual = state.get("actual_duty")
        self.actual_label.configure(
            text="BMC 自动" if actual == 0 else self._duty_text(actual)
        )
        mode = state.get("mode", "auto")
        self.mode_label.configure(text="手动" if mode == "manual" else "自动")

        auto = mode != "manual"
        self.auto_btn.configure(style="Accent.TButton" if auto else "TButton")
        self.manual_btn.configure(style="Accent.TButton" if not auto else "TButton")
        for button in (self.auto_btn, self.manual_btn):
            button.configure(state="normal" if control_enabled else "disabled")
        self.scale.configure(
            state="normal" if (control_enabled and not auto) else "disabled"
        )

        target = state.get("manual_duty" if not auto else "requested_duty")
        if not self._dragging and self._debounce is None and isinstance(target, (int, float)):
            self._suppress = True
            self.duty_var.set(max(min_duty, min(100, int(round(target)))))
            self._suppress = False
        self.duty_output.configure(text=f"{int(round(self.duty_var.get()))}%")

        if not control_enabled:
            self.mode_hint.configure(text="只读模式")
        elif auto:
            self.mode_hint.configure(text="自动曲线控制中")
        else:
            self.mode_hint.configure(text="拖动滑块调节，松开提交")
        alarm = state.get("alarm")
        self.alarm_label.configure(text=alarm or "")

    @staticmethod
    def _duty_text(value) -> str:
        return f"{value}%" if isinstance(value, (int, float)) else "—"


class CurvePanel(ttk.Frame):
    """可拖拽的自动温度曲线。"""

    def __init__(
        self,
        parent,
        fonts: dict,
        on_apply_curve: Callable[[list], None],
        on_reset: Callable[[], None],
    ):
        super().__init__(parent, style="Panel.TFrame", padding=12)
        self.fonts = fonts
        self.on_apply_curve = on_apply_curve
        self.on_reset = on_reset
        self.canvas = tk.Canvas(
            self, bg=COLORS["panel"], highlightthickness=0, height=240, takefocus=True
        )
        self.canvas.pack(fill="both", expand=True)

        header = ttk.Frame(self, style="Panel.TFrame")
        header.pack(fill="x", before=self.canvas)
        header.columnconfigure(0, weight=1)
        title_wrap = ttk.Frame(header, style="Panel.TFrame")
        title_wrap.grid(row=0, column=0, sticky="w")
        ttk.Label(title_wrap, style="Panel.TLabel", text="自动温度曲线", font=fonts["card_title"]).pack(anchor="w")
        ttk.Label(
            title_wrap,
            style="PanelMuted.TLabel",
            text="拖动蓝色控制点（30–80°C）调整占空比，松开自动保存",
        ).pack(anchor="w")

        right = ttk.Frame(header, style="Panel.TFrame")
        right.grid(row=0, column=1, sticky="e")
        self.status_label = ttk.Label(right, style="PanelMuted.TLabel", text="")
        self.status_label.pack(side="left", padx=(0, 10))
        self.reset_btn = ttk.Button(right, text="恢复默认曲线", command=self.on_reset)
        self.reset_btn.pack(side="left")

        self.readout = ttk.Label(self, style="PanelMuted.TLabel", text="")
        self.readout.pack(fill="x", pady=(6, 0))

        self.curve: list[list[float]] = [[0, 30], [60, 40], [85, 100], [100, 100]]
        self.gpus: list[dict] = []
        self.override_temp = 85.0
        self.min_duty = 20
        self._pending = False
        self._editable = True
        self._dragging = False
        self._drag_temp = None
        self._draft: list[list[float]] | None = None
        self._selected_temp = None
        self._save_timer = None
        self._geom = None
        self._handle_positions: dict[float, tuple[float, float]] = {}

        self.canvas.bind("<Configure>", lambda _event: self.redraw())
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_hover)
        self.canvas.bind("<Leave>", self._clear_hover)
        self.canvas.bind("<KeyPress>", self._on_key)
        self.winfo_toplevel().bind("<ButtonRelease-1>", self._global_release, add="+")

    # ---------------------------------------------------------------- 外部接口

    def update_state(self, snapshot: dict) -> None:
        limits = snapshot.get("limits") or {}
        curve = limits.get("auto_curve")
        if isinstance(curve, list) and len(curve) >= 2:
            self.curve = [[float(point[0]), int(point[1])] for point in curve]
        self.gpus = list(snapshot.get("gpus") or [])
        self.override_temp = float(limits.get("thermal_override_temp", 85))
        self.min_duty = int(limits.get("min_manual_duty", 20))
        if self._pending or self._dragging:
            return
        self.redraw()

    def set_status(self, text: str, kind: str = "muted") -> None:
        color = {
            "ok": COLORS["ok"],
            "error": COLORS["danger"],
            "pending": COLORS["muted"],
            "muted": COLORS["muted"],
        }.get(kind, COLORS["muted"])
        self.status_label.configure(text=text, foreground=color)

    def set_pending(self, pending: bool, text: str = "") -> None:
        self._pending = pending
        self.reset_btn.configure(
            state="disabled" if (pending or not self._editable) else "normal"
        )
        if text:
            self.set_status(text, "pending")

    def set_editable(self, editable: bool) -> None:
        self._editable = editable
        self.canvas.configure(cursor="" if editable else "watch")
        self.reset_btn.configure(
            state="disabled" if (not editable or self._pending) else "normal"
        )

    def apply_result(self, ok: bool, message: str) -> None:
        self._pending = False
        self.reset_btn.configure(state="normal" if self._editable else "disabled")
        self.set_status(message, "ok" if ok else "error")
        if ok:
            self.redraw()

    # ---------------------------------------------------------------- 绘制

    def redraw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width < 260 or height < 180:
            return
        left, right, top, bottom = 62, 24, 20, 54
        plot_w = width - left - right
        plot_h = height - top - bottom
        span = CURVE_X_MAX - CURVE_X_MIN

        def sx(temp):
            return left + (max(CURVE_X_MIN, min(CURVE_X_MAX, temp)) - CURVE_X_MIN) / span * plot_w

        def sy(duty):
            return top + (1 - max(0, min(100, duty)) / 100) * plot_h

        curve = self._draft if self._draft is not None else self.curve

        for duty in (0, 20, 40, 60, 80, 100):
            y = sy(duty)
            canvas.create_line(left, y, left + plot_w, y, fill=COLORS["grid"])
            canvas.create_text(left - 10, y, text=f"{duty}%", anchor="e", fill=COLORS["muted"], font=self.fonts["small"])
        for temp in range(CURVE_X_MIN, CURVE_X_MAX + 1, 10):
            x = sx(temp)
            canvas.create_line(x, top + plot_h, x, top + plot_h + 5, fill=COLORS["border"])
            canvas.create_text(x, top + plot_h + 19, text=f"{temp}°", anchor="n", fill=COLORS["muted"], font=self.fonts["small"])
        canvas.create_line(left, top, left, top + plot_h, fill=COLORS["border"])
        canvas.create_line(left, top + plot_h, left + plot_w, top + plot_h, fill=COLORS["border"])
        canvas.create_text(left + plot_w / 2, height - 8, text="GPU 温度（°C）", fill=COLORS["muted"], font=self.fonts["small"])
        canvas.create_text(14, top + plot_h / 2, text="风扇占空比（%）", angle=90, fill=COLORS["muted"], font=self.fonts["small"])

        points = [(CURVE_X_MIN, interpolate_curve(CURVE_X_MIN, curve))]
        for point in curve:
            if CURVE_X_MIN < point[0] < CURVE_X_MAX:
                points.append((point[0], point[1]))
        points.append((CURVE_X_MAX, interpolate_curve(CURVE_X_MAX, curve)))
        polygon = [sx(points[0][0]), sy(0)]
        for temp, duty in points:
            polygon.extend([sx(temp), sy(duty)])
        polygon.extend([sx(CURVE_X_MAX), sy(0)])
        canvas.create_polygon(polygon, fill=COLORS["area"], outline="")

        # 区域填充是不透明的，重画网格线保证其仍然可读
        for duty in (0, 20, 40, 60, 80, 100):
            y = sy(duty)
            canvas.create_line(left, y, left + plot_w, y, fill=COLORS["grid"])

        threshold_x = sx(self.override_temp)
        if threshold_x < left + plot_w:
            canvas.create_rectangle(
                threshold_x, top, left + plot_w, top + plot_h,
                fill=COLORS["band"], outline=""
            )
            canvas.create_line(threshold_x, top, threshold_x, top + plot_h, fill=COLORS["danger"])
            canvas.create_text(
                threshold_x + 6, top + 12, text=f"热保护 {self.override_temp:g}°C",
                anchor="w", fill=COLORS["danger"], font=self.fonts["small"]
            )

        line_points = []
        for temp, duty in points:
            line_points.extend([sx(temp), sy(duty)])
        canvas.create_line(*line_points, fill=COLORS["accent"], width=2, capstyle="round", joinstyle="round")

        # GPU 标记：重叠时纵向错开并用引线指回
        markers = []
        for gpu in self.gpus:
            temp = gpu.get("temperature_c")
            if not isinstance(temp, (int, float)) or gpu.get("sensor_stale"):
                continue
            duty = gpu.get("requested_duty", 0)
            markers.append((gpu, sx(temp), sy(duty)))
        markers.sort(key=lambda item: (item[2], item[1]))
        placed = []
        for gpu, x, y in markers:
            display_y = y
            for offset in (0, -22, 22, -44, 44, -66, 66):
                candidate = max(top + 10, min(top + plot_h - 10, y + offset))
                if all(abs(px - x) >= 20 or abs(py - candidate) >= 20 for px, py in placed):
                    display_y = candidate
                    break
            placed.append((x, display_y))
            if abs(display_y - y) > 1:
                canvas.create_line(x, y, x, display_y, fill=COLORS["marker"], width=1)
            canvas.create_oval(
                x - 8, display_y - 8, x + 8, display_y + 8,
                fill=COLORS["panel"], outline=COLORS["marker"], width=2
            )
            canvas.create_text(
                x, display_y, text=str(gpu["id"]), fill=COLORS["text"], font=self.fonts["small"]
            )

        # 可拖拽控制点
        self._handle_positions = {}
        for temp in HANDLE_TEMPS:
            duty = interpolate_curve(temp, curve)
            x, y = sx(temp), sy(duty)
            self._handle_positions[temp] = (x, y)
            selected = self._selected_temp == temp
            radius = 8 if selected else 7
            canvas.create_oval(
                x - radius, y - radius, x + radius, y + radius,
                fill=COLORS["panel"],
                outline=COLORS["accent_dark"] if selected else COLORS["accent"],
                width=3 if selected else 2,
            )

        self._geom = {
            "left": left, "top": top, "plot_w": plot_w, "plot_h": plot_h,
            "sx": sx, "sy": sy,
        }

    # ---------------------------------------------------------------- 交互

    def _find_handle(self, x: float, y: float):
        for temp, (hx, hy) in self._handle_positions.items():
            if abs(hx - x) <= 14 and abs(hy - y) <= 14:
                return temp
        return None

    def _on_press(self, event) -> None:
        if self._pending or not self._editable:
            return
        temp = self._find_handle(event.x, event.y)
        if temp is None:
            return
        self.canvas.focus_set()
        self._selected_temp = temp
        self._drag_temp = temp
        self._dragging = True
        self._draft = [[float(p[0]), int(p[1])] for p in self.curve]
        self._update_drag(event.y)
        self.redraw()

    def _on_motion(self, event) -> None:
        if not self._dragging or self._drag_temp is None or self._geom is None:
            return
        self._update_drag(event.y)
        self.redraw()

    def _update_drag(self, y: float) -> None:
        if self._draft is None or self._drag_temp is None or self._geom is None:
            return
        duty = int(round((self._geom["top"] + self._geom["plot_h"] - y) / self._geom["plot_h"] * 100))
        duty = max(self.min_duty, min(100, duty))
        set_curve_duty(self._draft, self._drag_temp, duty)
        self.readout.configure(text=f"{self._drag_temp:g}°C → {duty}%")

    def _on_release(self, _event=None) -> None:
        self._finish_drag()

    def _global_release(self, _event=None) -> None:
        if self._dragging:
            self._finish_drag()

    def _finish_drag(self) -> None:
        if not self._dragging:
            return
        self._dragging = False
        if self._draft is not None:
            draft = [[float(p[0]), int(p[1])] for p in self._draft]
            self._draft = None
            self.set_pending(True, "正在保存曲线…")
            self.on_apply_curve(draft)
        self._drag_temp = None

    def _on_hover(self, event) -> None:
        if self._dragging or self._geom is None:
            return
        geom = self._geom
        if not (geom["left"] <= event.x <= geom["left"] + geom["plot_w"]):
            self._clear_hover()
            return
        if self._find_handle(event.x, event.y) is not None:
            self.canvas.configure(cursor="sb_v_double_arrow")
        else:
            self.canvas.configure(cursor="")
        temp = CURVE_X_MIN + (event.x - geom["left"]) / geom["plot_w"] * (CURVE_X_MAX - CURVE_X_MIN)
        duty = interpolate_curve(temp, self._draft if self._draft is not None else self.curve)
        self.readout.configure(text=f"{temp:.0f}°C → {duty}%")

    def _clear_hover(self, _event=None) -> None:
        if not self._dragging:
            self.readout.configure(text="")

    def _on_key(self, event) -> None:
        if self._pending or not self._editable:
            return
        if self._selected_temp is None:
            self._selected_temp = 60
        current = interpolate_curve(
            self._selected_temp, self._draft if self._draft is not None else self.curve
        )
        delta = 0
        if event.keysym in {"Up", "Right"}:
            delta = 5 if event.state & 0x0001 else 1
        elif event.keysym in {"Down", "Left"}:
            delta = -5 if event.state & 0x0001 else -1
        elif event.keysym == "Prior":
            delta = 10
        elif event.keysym == "Next":
            delta = -10
        elif event.keysym == "Home":
            delta = -1000
        elif event.keysym == "End":
            delta = 1000
        else:
            return
        if self._draft is None:
            self._draft = [[float(p[0]), int(p[1])] for p in self.curve]
        duty = max(self.min_duty, min(100, current + delta))
        set_curve_duty(self._draft, self._selected_temp, duty)
        self.readout.configure(text=f"{self._selected_temp:g}°C → {duty}%")
        self.redraw()
        if self._save_timer is not None:
            self.after_cancel(self._save_timer)
        self._save_timer = self.after(450, self._finish_keyboard_drag)

    def _finish_keyboard_drag(self) -> None:
        self._save_timer = None
        if self._draft is None:
            return
        draft = [[float(p[0]), int(p[1])] for p in self._draft]
        self._draft = None
        self.set_pending(True, "正在保存曲线…")
        self.on_apply_curve(draft)
