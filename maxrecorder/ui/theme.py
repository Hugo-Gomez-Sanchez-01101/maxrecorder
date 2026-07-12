"""Color palettes and themed widgets: buttons, status LED, audio visualizer,
progress bar and construction helpers.

Two themes are available:
  - "dark": the original neon-on-dark console look.
  - "te":   a Teenage Engineering inspired look (warm light gray, black ink,
            TE orange accents).

`P` is a mutable palette: `set_theme(name)` swaps every color in place. Widgets
read `P` when they are constructed, so switching themes at runtime requires
rebuilding the UI (App._apply_theme takes care of that)."""

import math
import random
import tkinter as tk


THEMES = {
    "dark": dict(
        BG="#0a0e13",          # general background
        PANEL="#0f151d",       # panels / sections
        PANEL2="#121a24",      # raised panels
        FIELD="#0b1017",       # text fields
        BORDER="#1f2d3b",      # thin borders
        GRID="#14202c",        # visualizer grid
        TEXT="#d6e3ee",        # primary text
        DIM="#5d7183",         # secondary text
        ACCENT="#00e5ff",      # neon cyan (identity)
        ACCENT_DK="#073a44",   # dim cyan (selections)
        GREEN="#2be8a6",       # ok / ready
        RED="#ff3860",         # recording / danger
        RED_DK="#4a1220",
        AMBER="#ffb454",       # processing / warning
        LED_IDLE="#3a4a5a",
        DISABLED_FG="#3c4b5a",
        # (bg, fg, hover_bg) per button kind
        BTN_PRIMARY=("#073a44", "#00e5ff", "#0a5666"),
        BTN_DANGER=("#4a1220", "#ff8ba3", "#6b1a2e"),
        BTN_GHOST=("#121a24", "#d6e3ee", "#1a2634"),
    ),
    "te": dict(
        BG="#e6e6e2",          # warm light gray (TE plastic)
        PANEL="#dcdcd8",
        PANEL2="#d2d2ce",
        FIELD="#f5f5f2",       # near-white fields
        BORDER="#b9b9b4",
        GRID="#cfcfca",
        TEXT="#141414",        # black ink
        DIM="#8b8b86",
        ACCENT="#ff4b00",      # TE orange
        ACCENT_DK="#ffd2bd",   # pale orange (selections)
        GREEN="#00a353",
        RED="#e03616",
        RED_DK="#f3c1b5",
        AMBER="#e89b00",
        LED_IDLE="#b3b3ae",
        DISABLED_FG="#a3a39e",
        BTN_PRIMARY=("#ff4b00", "#ffffff", "#d94000"),
        BTN_DANGER=("#141414", "#ff6a3d", "#2e2e2c"),
        BTN_GHOST=("#d2d2ce", "#141414", "#c4c4c0"),
    ),
}

DEFAULT_THEME = "dark"


class P:
    """Mutable palette. Colors are injected by set_theme(); fonts are shared
    by both themes."""

    NAME = DEFAULT_THEME

    FONT = ("Segoe UI", 9)
    FONT_SM = ("Segoe UI", 8)
    FONT_BOLD = ("Segoe UI", 9, "bold")
    MONO = ("Consolas", 10)
    MONO_BIG = ("Consolas", 22, "bold")
    TITLE = ("Consolas", 15, "bold")


def set_theme(name: str):
    """Swaps every color in P for the given theme ('dark' or 'te')."""
    values = THEMES.get(name)
    if values is None:
        name, values = DEFAULT_THEME, THEMES[DEFAULT_THEME]
    for key, value in values.items():
        setattr(P, key, value)
    P.NAME = name


set_theme(DEFAULT_THEME)


def blend(c1: str, c2: str, t: float) -> str:
    """Interpolate two hex colors '#rrggbb' (t=0 -> c1, t=1 -> c2)."""
    t = min(max(t, 0.0), 1.0)
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(x + (y - x) * t) for x, y in zip(a, b))


def _widget_alive(widget) -> bool:
    """True if the widget still exists (guards animation loops after the UI is
    rebuilt on a theme switch)."""
    try:
        return bool(widget.winfo_exists())
    except tk.TclError:
        return False


class TechButton(tk.Button):
    """Flat console-style button with hover effect. kind: 'primary' (accent),
    'danger', 'ghost' (transparent with border). Colors are taken from the
    active theme at construction time."""

    def __init__(self, master, kind="ghost", **kw):
        kinds = {
            "primary": P.BTN_PRIMARY,
            "danger": P.BTN_DANGER,
            "ghost": P.BTN_GHOST,
        }
        bg, fg, hover = kinds.get(kind, kinds["ghost"])
        self._bg, self._hover = bg, hover
        super().__init__(
            master, relief="flat", bd=0, cursor="hand2",
            bg=bg, fg=fg, activebackground=hover, activeforeground=fg,
            disabledforeground=P.DISABLED_FG, font=P.FONT_BOLD,
            padx=14, pady=5, highlightthickness=1,
            highlightbackground=P.BORDER, highlightcolor=P.BORDER, **kw)
        self.bind("<Enter>", lambda e: self._set_bg(self._hover))
        self.bind("<Leave>", lambda e: self._set_bg(self._bg))

    def _set_bg(self, color):
        if self["state"] != "disabled":
            self.config(bg=color)


class StatusLED(tk.Canvas):
    """Circular LED with an animated pulse. States: idle (gray), ready (green),
    watching (accent), recording (red), busy (amber)."""

    def __init__(self, master, size=14, **kw):
        super().__init__(master, width=size, height=size, bg=kw.pop("bg", P.PANEL),
                         highlightthickness=0, **kw)
        self.size = size
        self._state = "idle"
        self._phase = 0.0
        m = 2
        self._dot = self.create_oval(m, m, size - m, size - m, fill=P.LED_IDLE, outline="")
        self._animate()

    def set_state(self, state):
        self._state = state

    def _animate(self):
        if not _widget_alive(self):
            return
        # Read from P at runtime so the pulse always uses the active theme.
        colors = {
            "idle": (P.LED_IDLE, False),
            "ready": (P.GREEN, False),
            "watching": (P.ACCENT, True),
            "recording": (P.RED, True),
            "busy": (P.AMBER, True),
        }
        color, pulse = colors.get(self._state, colors["idle"])
        if pulse:
            self._phase += 0.18
            t = (math.sin(self._phase) + 1) / 2  # 0..1
            color = blend(blend(color, P.BG, 0.65), color, t)
        self.itemconfig(self._dot, fill=color)
        self.after(60, self._animate)


class AudioVisualizer(tk.Canvas):
    """Bar oscilloscope scrolling right to left. While recording, the bar
    heights come from the real RMS levels (system and microphone); at rest it
    draws a faint scanning wave."""

    def __init__(self, master, height=88, **kw):
        super().__init__(master, height=height, bg=P.PANEL,
                         highlightthickness=1, highlightbackground=P.BORDER, **kw)
        self.h = height
        self.recording = False
        self.level_source = None  # callable -> (sys_level, mic_level) in 0..1
        self._history = []        # (sys_level, mic_level) per column
        self._phase = 0.0
        self._bar_w = 3
        self._gap = 2
        self._animate()

    def _columns(self):
        w = max(self.winfo_width(), 100)
        return max(w // (self._bar_w + self._gap), 10), w

    def _animate(self):
        if not _widget_alive(self):
            return
        ncols, w = self._columns()
        self._phase += 0.12

        if self.recording and self.level_source:
            s, m = self.level_source()
            # perceptual scaling + a bit of life (jitter)
            s = min(math.sqrt(max(s, 0.0)) * 1.6, 1.0) * (0.85 + 0.3 * random.random())
            m = min(math.sqrt(max(m, 0.0)) * 1.6, 1.0) * (0.85 + 0.3 * random.random())
            self._history.append((min(s, 1.0), min(m, 1.0)))
        else:
            # idle wave: soft breathing with a double sine
            t = self._phase
            v = 0.06 + 0.045 * (math.sin(t) * math.sin(t * 0.37) + 1) / 2
            self._history.append((v, v * 0.7))
        self._history = self._history[-ncols:]

        self.delete("all")
        cy = self.h // 2
        # grid
        self.create_line(0, cy, w, cy, fill=P.GRID)
        for gx in range(0, w, 60):
            self.create_line(gx, 0, gx, self.h, fill=P.GRID)

        x = w - len(self._history) * (self._bar_w + self._gap)
        max_h = self.h // 2 - 6
        for s, m in self._history:
            if self.recording:
                # system (accent to red by intensity) upward,
                # microphone (green) downward
                hs = max(int(s * max_h), 1)
                hm = max(int(m * max_h), 1)
                cs = blend(P.ACCENT, P.RED, s * s)
                cm = blend(P.GREEN, P.AMBER, m * m)
                self.create_rectangle(x, cy - hs, x + self._bar_w, cy, fill=cs, outline="")
                self.create_rectangle(x, cy, x + self._bar_w, cy + hm, fill=cm, outline="")
            else:
                hv = max(int(s * max_h), 1)
                c = blend(P.GRID, P.ACCENT, 0.45)
                self.create_rectangle(x, cy - hv, x + self._bar_w, cy + hv, fill=c, outline="")
            x += self._bar_w + self._gap

        if self.recording:
            # minimal legend
            self.create_text(8, 10, text="SYS", anchor="w", fill=P.ACCENT, font=("Consolas", 8))
            self.create_text(8, self.h - 10, text="MIC", anchor="w", fill=P.GREEN, font=("Consolas", 8))

        self.after(45, self._animate)


class TechProgress(tk.Canvas):
    """Progress bar: determinate (fraction 0..1) or indeterminate (animated
    scanning band)."""

    def __init__(self, master, height=6, **kw):
        super().__init__(master, height=height, bg=P.FIELD,
                         highlightthickness=1, highlightbackground=P.BORDER, **kw)
        self.h = height
        self._value = -1.0
        self._scan = 0.0
        self._animate()

    def set(self, value):
        # None -> indeterminate (-2); [0..1] -> determinate. hide() hides it (-1).
        self._value = -2.0 if value is None else float(value)

    def hide(self):
        self._value = -1.0

    def _animate(self):
        if not _widget_alive(self):
            return
        self.delete("all")
        w = max(self.winfo_width(), 10)
        if self._value >= 0:
            fill_w = int(w * min(self._value, 1.0))
            self.create_rectangle(0, 0, fill_w, self.h, fill=P.ACCENT, outline="")
        elif self._value <= -2.0:
            self._scan += 0.03
            band = w // 4
            x = int((self._scan % 1.0) * (w + band)) - band
            self.create_rectangle(x, 0, x + band, self.h, fill=P.ACCENT, outline="")
        self.after(40, self._animate)


def make_section(parent, title):
    """Creates a panel-style section with a header and returns (outer frame,
    inner frame where the widgets go)."""
    outer = tk.Frame(parent, bg=P.PANEL, highlightthickness=1,
                     highlightbackground=P.BORDER)
    outer.pack(fill="x", padx=10, pady=(8, 0))
    header = tk.Frame(outer, bg=P.PANEL)
    header.pack(fill="x")
    tk.Label(header, text="▸ " + title.upper(), bg=P.PANEL, fg=P.ACCENT,
             font=("Consolas", 9, "bold"), anchor="w").pack(side="left", padx=10, pady=(6, 2))
    inner = tk.Frame(outer, bg=P.PANEL)
    inner.pack(fill="both", expand=True, padx=8, pady=(0, 8))
    return outer, inner


def make_collapsible_section(parent, title, expanded=False):
    """Like make_section but the body collapses/expands when the header is
    clicked. Returns (outer frame, inner frame)."""
    outer = tk.Frame(parent, bg=P.PANEL, highlightthickness=1,
                     highlightbackground=P.BORDER)
    outer.pack(fill="x", padx=10, pady=(8, 0))
    header = tk.Frame(outer, bg=P.PANEL, cursor="hand2")
    header.pack(fill="x")
    arrow = tk.Label(header, text="▾" if expanded else "▸", bg=P.PANEL,
                     fg=P.ACCENT, font=("Consolas", 9, "bold"), cursor="hand2")
    arrow.pack(side="left", padx=(10, 0), pady=(6, 4))
    lbl = tk.Label(header, text=" " + title.upper(), bg=P.PANEL, fg=P.ACCENT,
                   font=("Consolas", 9, "bold"), anchor="w", cursor="hand2")
    lbl.pack(side="left", pady=(6, 4))
    inner = tk.Frame(outer, bg=P.PANEL)
    if expanded:
        inner.pack(fill="both", expand=True, padx=8, pady=(0, 8))
    state = {"open": expanded}

    def toggle(_event=None):
        state["open"] = not state["open"]
        if state["open"]:
            inner.pack(fill="both", expand=True, padx=8, pady=(0, 8))
            arrow.config(text="▾")
        else:
            inner.pack_forget()
            arrow.config(text="▸")

    for w in (header, arrow, lbl):
        w.bind("<Button-1>", toggle)
    return outer, inner


def dark_entry(parent, **kw):
    return tk.Entry(parent, bg=P.FIELD, fg=P.TEXT, insertbackground=P.ACCENT,
                    relief="flat", highlightthickness=1,
                    highlightbackground=P.BORDER, highlightcolor=P.ACCENT,
                    font=P.FONT, **kw)


def dark_check(parent, **kw):
    return tk.Checkbutton(parent, bg=P.PANEL, fg=P.TEXT, activebackground=P.PANEL,
                          activeforeground=P.TEXT, selectcolor=P.FIELD,
                          font=P.FONT, anchor="w", **kw)


def dark_label(parent, dim=False, **kw):
    kw.setdefault("font", P.FONT_SM if dim else P.FONT)
    return tk.Label(parent, bg=kw.pop("bg", P.PANEL), fg=P.DIM if dim else P.TEXT, **kw)
