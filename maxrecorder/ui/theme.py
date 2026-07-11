"""Paleta de colores y widgets del tema oscuro: botones, LED de estado,
visualizador de audio, barra de progreso y ayudantes de construcción."""

import math
import random
import tkinter as tk


class P:
    BG = "#0a0e13"          # fondo general
    PANEL = "#0f151d"       # paneles / secciones
    PANEL2 = "#121a24"      # paneles elevados
    FIELD = "#0b1017"       # campos de texto
    BORDER = "#1f2d3b"      # bordes finos
    GRID = "#14202c"        # rejilla del visualizador
    TEXT = "#d6e3ee"        # texto principal
    DIM = "#5d7183"         # texto secundario
    ACCENT = "#00e5ff"      # cian neón (identidad)
    ACCENT_DK = "#073a44"   # cian apagado (fondos de botón)
    GREEN = "#2be8a6"       # ok / listo
    RED = "#ff3860"         # grabando / peligro
    RED_DK = "#4a1220"
    AMBER = "#ffb454"       # procesando / aviso

    FONT = ("Segoe UI", 9)
    FONT_SM = ("Segoe UI", 8)
    FONT_BOLD = ("Segoe UI", 9, "bold")
    MONO = ("Consolas", 10)
    MONO_BIG = ("Consolas", 22, "bold")
    TITLE = ("Consolas", 15, "bold")


def blend(c1: str, c2: str, t: float) -> str:
    """Interpola dos colores hex '#rrggbb' (t=0 -> c1, t=1 -> c2)."""
    t = min(max(t, 0.0), 1.0)
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(x + (y - x) * t) for x, y in zip(a, b))


class TechButton(tk.Button):
    """Botón plano estilo consola con efecto hover. kind: 'primary' (cian),
    'danger' (rojo), 'ghost' (transparente con borde)."""

    KINDS = {
        "primary": (P.ACCENT_DK, P.ACCENT, "#0a5666"),
        "danger": (P.RED_DK, "#ff8ba3", "#6b1a2e"),
        "ghost": (P.PANEL2, P.TEXT, "#1a2634"),
    }

    def __init__(self, master, kind="ghost", **kw):
        bg, fg, hover = self.KINDS.get(kind, self.KINDS["ghost"])
        self._bg, self._hover = bg, hover
        super().__init__(
            master, relief="flat", bd=0, cursor="hand2",
            bg=bg, fg=fg, activebackground=hover, activeforeground=fg,
            disabledforeground="#3c4b5a", font=P.FONT_BOLD,
            padx=14, pady=5, highlightthickness=1,
            highlightbackground=P.BORDER, highlightcolor=P.BORDER, **kw)
        self.bind("<Enter>", lambda e: self._set_bg(self._hover))
        self.bind("<Leave>", lambda e: self._set_bg(self._bg))

    def _set_bg(self, color):
        if self["state"] != "disabled":
            self.config(bg=color)


class StatusLED(tk.Canvas):
    """LED circular con pulso animado. Estados: idle (gris), ready (verde),
    watching (cian), recording (rojo), busy (ámbar)."""

    COLORS = {
        "idle": ("#3a4a5a", False),
        "ready": (P.GREEN, False),
        "watching": (P.ACCENT, True),
        "recording": (P.RED, True),
        "busy": (P.AMBER, True),
    }

    def __init__(self, master, size=14, **kw):
        super().__init__(master, width=size, height=size, bg=kw.pop("bg", P.PANEL),
                         highlightthickness=0, **kw)
        self.size = size
        self._state = "idle"
        self._phase = 0.0
        m = 2
        self._dot = self.create_oval(m, m, size - m, size - m, fill="#3a4a5a", outline="")
        self._animate()

    def set_state(self, state):
        self._state = state if state in self.COLORS else "idle"

    def _animate(self):
        color, pulse = self.COLORS[self._state]
        if pulse:
            self._phase += 0.18
            t = (math.sin(self._phase) + 1) / 2  # 0..1
            color = blend(blend(color, P.BG, 0.65), color, t)
        self.itemconfig(self._dot, fill=color)
        self.after(60, self._animate)


class AudioVisualizer(tk.Canvas):
    """Osciloscopio de barras desplazándose de derecha a izquierda. Mientras se
    graba, la altura de las barras viene de los niveles RMS reales (sistema y
    micrófono); en reposo dibuja una onda de escaneo tenue."""

    def __init__(self, master, height=88, **kw):
        super().__init__(master, height=height, bg=P.PANEL,
                         highlightthickness=1, highlightbackground=P.BORDER, **kw)
        self.h = height
        self.recording = False
        self.level_source = None  # callable -> (sys_level, mic_level) en 0..1
        self._history = []        # (nivel_sys, nivel_mic) por columna
        self._phase = 0.0
        self._bar_w = 3
        self._gap = 2
        self._animate()

    def _columns(self):
        w = max(self.winfo_width(), 100)
        return max(w // (self._bar_w + self._gap), 10), w

    def _animate(self):
        ncols, w = self._columns()
        self._phase += 0.12

        if self.recording and self.level_source:
            s, m = self.level_source()
            # escala perceptual + un poco de vida (jitter)
            s = min(math.sqrt(max(s, 0.0)) * 1.6, 1.0) * (0.85 + 0.3 * random.random())
            m = min(math.sqrt(max(m, 0.0)) * 1.6, 1.0) * (0.85 + 0.3 * random.random())
            self._history.append((min(s, 1.0), min(m, 1.0)))
        else:
            # onda idle: respiración suave con doble seno
            t = self._phase
            v = 0.06 + 0.045 * (math.sin(t) * math.sin(t * 0.37) + 1) / 2
            self._history.append((v, v * 0.7))
        self._history = self._history[-ncols:]

        self.delete("all")
        cy = self.h // 2
        # rejilla
        self.create_line(0, cy, w, cy, fill=P.GRID)
        for gx in range(0, w, 60):
            self.create_line(gx, 0, gx, self.h, fill=P.GRID)

        x = w - len(self._history) * (self._bar_w + self._gap)
        max_h = self.h // 2 - 6
        for s, m in self._history:
            if self.recording:
                # sistema (cian a magenta según intensidad) hacia arriba,
                # micrófono (verde) hacia abajo
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
            # leyenda mínima
            self.create_text(8, 10, text="SYS", anchor="w", fill=P.ACCENT, font=("Consolas", 8))
            self.create_text(8, self.h - 10, text="MIC", anchor="w", fill=P.GREEN, font=("Consolas", 8))

        self.after(45, self._animate)


class TechProgress(tk.Canvas):
    """Barra de progreso: determinada (fracción 0..1) o indeterminada (banda
    de escaneo animada)."""

    def __init__(self, master, height=6, **kw):
        super().__init__(master, height=height, bg=P.FIELD,
                         highlightthickness=1, highlightbackground=P.BORDER, **kw)
        self.h = height
        self._value = -1.0
        self._scan = 0.0
        self._animate()

    def set(self, value):
        # None -> indeterminada (-2); [0..1] -> determinada. hide() la oculta (-1).
        self._value = -2.0 if value is None else float(value)

    def hide(self):
        self._value = -1.0

    def _animate(self):
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
    """Crea una sección estilo panel con cabecera y devuelve (frame exterior,
    frame interior donde colocar los widgets)."""
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
