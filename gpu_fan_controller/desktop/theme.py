import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

COLORS = {
    "bg": "#eef1f5",
    "panel": "#ffffff",
    "panel_alt": "#f6f8fa",
    "border": "#d0d7de",
    "text": "#1f2328",
    "muted": "#59636e",
    "accent": "#0969da",
    "accent_dark": "#0550ae",
    "ok": "#1a7f37",
    "warn": "#9a6700",
    "danger": "#cf222e",
    "protect": "#8250df",
    "stale": "#6e7781",
    "grid": "#e1e0d9",
    "area": "#dbe9fb",
    "band": "#fbeced",
    "marker": "#eb6834",
    "white": "#ffffff",
}


def choose_font(root: tk.Misc) -> str:
    families = set(tkfont.families(root))
    candidates = (
        "Microsoft YaHei UI",
        "Microsoft YaHei",
        "Noto Sans CJK SC",
        "Noto Sans SC",
        "WenQuanYi Micro Hei",
        "PingFang SC",
    )
    for name in candidates:
        if name in families:
            return name
    return tkfont.nametofont("TkDefaultFont").actual("family")


def apply_theme(root: tk.Tk) -> dict:
    """应用浅色主题，返回字体/颜色配置。"""
    family = choose_font(root)
    base = (family, 10)
    small = (family, 9)
    bold = (family, 10, "bold")
    title = (family, 17, "bold")
    card_title = (family, 12, "bold")
    temp_font = (family, 20, "bold")

    style = ttk.Style(root)
    themes = style.theme_names()
    if sys.platform == "win32" and "vista" in themes:
        style.theme_use("vista")
    elif "clam" in themes:
        style.theme_use("clam")

    root.configure(bg=COLORS["bg"])
    style.configure(".", font=base, background=COLORS["bg"], foreground=COLORS["text"])
    style.configure("TFrame", background=COLORS["bg"])
    style.configure("Panel.TFrame", background=COLORS["panel"])
    style.configure("Card.TFrame", background=COLORS["panel"])
    style.configure("Header.TFrame", background=COLORS["panel"])
    style.configure(
        "TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=base
    )
    style.configure("Panel.TLabel", background=COLORS["panel"])
    style.configure("Card.TLabel", background=COLORS["panel"], font=base)
    style.configure("CardTitle.TLabel", background=COLORS["panel"], font=card_title)
    style.configure("CardMuted.TLabel", background=COLORS["panel"], foreground=COLORS["muted"], font=small)
    style.configure("Temp.TLabel", background=COLORS["panel"], font=temp_font)
    style.configure("Title.TLabel", background=COLORS["panel"], font=title)
    style.configure("Muted.TLabel", foreground=COLORS["muted"], font=small)
    style.configure("PanelMuted.TLabel", background=COLORS["panel"], foreground=COLORS["muted"], font=small)
    style.configure("Status.TLabel", background=COLORS["panel"], font=bold)
    style.configure("Badge.TLabel", background=COLORS["panel"], font=small, padding=(8, 2))
    style.configure("TButton", padding=(10, 5))
    style.configure(
        "Accent.TButton", background=COLORS["accent"], foreground=COLORS["white"]
    )
    style.map(
        "Accent.TButton",
        background=[("active", COLORS["accent_dark"]), ("disabled", COLORS["border"])],
        foreground=[("disabled", COLORS["muted"])],
    )
    style.configure(
        "Danger.TButton", background=COLORS["danger"], foreground=COLORS["white"]
    )
    style.map("Danger.TButton", background=[("active", "#a40e1b")])
    style.configure("TNotebook", background=COLORS["bg"], borderwidth=0)
    style.configure(
        "TNotebook.Tab",
        padding=(16, 7),
        background=COLORS["panel_alt"],
        foreground=COLORS["muted"],
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", COLORS["panel"])],
        foreground=[("selected", COLORS["accent"])],
    )
    style.configure(
        "Treeview",
        background=COLORS["panel"],
        fieldbackground=COLORS["panel"],
        foreground=COLORS["text"],
        rowheight=27,
        borderwidth=0,
    )
    style.map("Treeview", background=[("selected", COLORS["area"])], foreground=[("selected", COLORS["text"])])
    style.configure(
        "Treeview.Heading",
        background=COLORS["panel_alt"],
        foreground=COLORS["muted"],
        font=bold,
        relief="flat",
    )
    style.configure("TEntry", fieldbackground=COLORS["white"], padding=4)
    style.configure("TCombobox", fieldbackground=COLORS["white"], padding=3)
    style.configure(
        "TLabelframe", background=COLORS["panel"], bordercolor=COLORS["border"], padding=10
    )
    style.configure("TLabelframe.Label", background=COLORS["panel"], foreground=COLORS["muted"], font=bold)
    style.configure("TCheckbutton", background=COLORS["panel"], font=base)
    style.configure("TScale", background=COLORS["panel"])
    style.configure("TScrollbar", background=COLORS["panel_alt"], troughcolor=COLORS["panel_alt"])
    style.configure("TSeparator", background=COLORS["border"])

    return {
        "colors": COLORS,
        "font": family,
        "base": base,
        "small": small,
        "bold": bold,
        "title": title,
        "card_title": card_title,
        "temp": temp_font,
    }
