"""
Max Recorder — Reuniones de Teams (Sistema + Micrófono) con Transcripción
=========================================================================

Graba simultáneamente:
  - El audio de salida de Windows (lo que suena por los altavoces/lo que dicen los
    demás en Teams) vía WASAPI loopback.
  - Tu micrófono.
Mezcla ambas pistas en un único WAV con alineación temporal correcta (corrige el
offset de arranque entre hilos y el drift de reloj entre dispositivos) y transcribe
con faster-whisper (local, gratis). El resumen de las transcripciones se genera
aparte mediante una tarea programada de Claude, no desde esta app.

Transcripción mejorada:
  - El modelo Whisper se carga una sola vez y se reutiliza (caché en memoria).
  - Filtro VAD (detección de voz) para saltar silencios: más rápido y menos
    alucinaciones en tramos sin habla.
  - Resultado en streaming: los segmentos van apareciendo según se transcriben,
    con marcas de tiempo y barra de progreso.
  - Modo "Tú / Ellos": transcribe las pistas _mic y _sistema por separado y
    entrelaza los segmentos por tiempo, etiquetando quién habla.
  - Autoguardado del .txt junto a la grabación y transcripción automática
    opcional al detener.

Además puede quedarse en segundo plano (bandeja del sistema) y detectar cuándo
arranca una reunión de Teams para mostrar un aviso y ofrecer grabar con un clic.
Puede arrancar automáticamente al iniciar sesión (minimizada a la bandeja); ver
la casilla de "inicio automático" o el argumento --tray.

Solo funciona en Windows (usa PyAudioWPatch / WASAPI loopback).
"""

import os
import sys
import json
import math
import time
import wave
import random
import argparse
import threading
import faulthandler
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime

import numpy as np

try:
    import pyaudiowpatch as pyaudio
except ImportError:
    pyaudio = None

try:
    from faster_whisper import WhisperModel
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False

try:
    import winreg
    WINREG_AVAILABLE = True
except ImportError:
    WINREG_AVAILABLE = False

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

try:
    import win32gui
    import win32process
    WIN32_AVAILABLE = True
except ImportError:
    WIN32_AVAILABLE = False

try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False

try:
    from scipy.signal import fftconvolve
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False


CHUNK = 1024
FORMAT_WIDTH = 2  # int16
TARGET_RATE = 44100
_DOCS_MAX_RECORDER = os.path.join(os.path.expanduser("~"), "Documents", "MaxRecorder")
OUTPUT_DIR_DEFAULT = os.path.join(_DOCS_MAX_RECORDER, "Records")
TRANSCRIPT_DIR_DEFAULT = os.path.join(_DOCS_MAX_RECORDER, "Transcripts")

TEAMS_PROCESS_NAMES = {"teams.exe", "ms-teams.exe"}
DEFAULT_MEETING_KEYWORDS = ["reunión", "reunion", "meeting", "llamada", "call", "[Weekly] Hacking Team"]

# Detección de "en reunión" por uso del micrófono (fiable, sin depender del
# título de ventana). Windows registra aquí, por app, cuándo empezó/terminó de
# usar el micro; si LastUsedTimeStop == 0, la app lo está usando AHORA mismo.
MIC_CONSENT_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
    r"\ConsentStore\microphone"
)

# Clave de arranque automático al iniciar sesión (por usuario, sin admin)
AUTOSTART_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_VALUE_NAME = "MaxRecorder"
# Nombre antiguo (versiones previas); se limpia al desactivar el inicio automático.
AUTOSTART_LEGACY_NAMES = ("GrabadorTeams",)

# Nombre del .txt de transcripción: por defecto reunion_AAAA-MM-DD.txt. Si el
# título de alguna ventana de Teams al empezar a grabar contiene una de estas
# subcadenas, se usa su prefijo en su lugar (p.ej. weekly_AAAA-MM-DD.txt).
DEFAULT_TRANSCRIPT_PREFIX = "reunion"
MEETING_NAME_RULES = [
    ("[weekly] hacking team", "weekly"),
]

# Ajustes persistentes (carpetas, palabras clave, sondeo) junto al script.
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def save_config(cfg: dict):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Paleta y utilidades visuales (tema oscuro "tech")
# --------------------------------------------------------------------------

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
    """Interpola dos colores hex '#rrggbb' (t=0 → c1, t=1 → c2)."""
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
                # sistema (cian→magenta según intensidad) hacia arriba,
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
    de escaneo animada). set(None) la deja indeterminada; set(-1) la oculta."""

    def __init__(self, master, height=6, **kw):
        super().__init__(master, height=height, bg=P.FIELD,
                         highlightthickness=1, highlightbackground=P.BORDER, **kw)
        self.h = height
        self._value = -1.0
        self._scan = 0.0
        self._animate()

    def set(self, value):
        # None → indeterminada (-2); [0..1] → determinada. hide() la oculta (-1).
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
    """Crea una sección estilo panel con cabecera '▸ TÍTULO' y devuelve el
    frame interior donde colocar los widgets."""
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


# --------------------------------------------------------------------------
# Arranque automático al iniciar sesión (clave Run del registro de Windows)
# --------------------------------------------------------------------------

def _autostart_command() -> str:
    """Comando que Windows ejecutará al iniciar sesión. Arranca la app en modo
    bandeja (--tray). Si está congelada con PyInstaller usa el propio .exe;
    si no, usa pythonw.exe (sin consola) sobre este script."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --tray'
    script = os.path.abspath(__file__)
    exe = sys.executable
    # pythonw.exe evita la ventana de consola al arrancar en segundo plano
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pythonw):
        exe = pythonw
    return f'"{exe}" "{script}" --tray'


def is_autostart_enabled() -> bool:
    if not WINREG_AVAILABLE:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY) as key:
            winreg.QueryValueEx(key, AUTOSTART_VALUE_NAME)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def enable_autostart():
    if not WINREG_AVAILABLE:
        raise RuntimeError("El registro de Windows no está disponible en esta plataforma.")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY) as key:
        winreg.SetValueEx(key, AUTOSTART_VALUE_NAME, 0, winreg.REG_SZ, _autostart_command())


def disable_autostart():
    if not WINREG_AVAILABLE:
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            for name in (AUTOSTART_VALUE_NAME,) + AUTOSTART_LEGACY_NAMES:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
    except FileNotFoundError:
        pass


# --------------------------------------------------------------------------
# Utilidades de audio: resample, alineación temporal y mezcla
# --------------------------------------------------------------------------

def resample_linear(data: np.ndarray, orig_rate: float, target_rate: int) -> np.ndarray:
    """Resamplea un array 1D (mono) a target_rate. orig_rate puede ser la tasa
    "efectiva" real (nº muestras / duración real en reloj de pared), lo que
    corrige el drift entre el reloj del dispositivo y el tiempo real."""
    if len(data) == 0 or orig_rate <= 0:
        return data.astype(np.float32)
    duration = len(data) / orig_rate
    n_target = max(int(round(duration * target_rate)), 0)
    if n_target == 0:
        return np.zeros(0, dtype=np.float32)
    x_orig = np.linspace(0, duration, num=len(data), endpoint=False)
    x_target = np.linspace(0, duration, num=n_target, endpoint=False)
    return np.interp(x_target, x_orig, data).astype(np.float32)


def to_mono(samples: np.ndarray, channels: int) -> np.ndarray:
    if channels <= 1:
        return samples.astype(np.float32)
    reshaped = samples[: len(samples) - (len(samples) % channels)].reshape(-1, channels)
    return reshaped.mean(axis=1).astype(np.float32)


def bytes_to_float_mono(raw: bytes, channels: int) -> np.ndarray:
    arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    return to_mono(arr, max(channels, 1))


def pad_front(arr: np.ndarray, n_samples: int) -> np.ndarray:
    if n_samples <= 0:
        return arr
    return np.concatenate([np.zeros(n_samples, dtype=np.float32), arr])


def estimate_fine_shift(reference: np.ndarray, other: np.ndarray, rate: int,
                         max_shift_sec: float = 1.0, corr_threshold: float = 0.25,
                         window_sec: float = 20.0) -> int:
    """Corrige un desfase residual (milisegundos) entre pistas usando
    correlación cruzada sobre una ventana inicial, aprovechando que el mic
    suele captar una fuga tenue del audio de los altavoces. Devuelve el
    desplazamiento en muestras a aplicar sobre 'other' (positivo = retrasar
    'other'). Si la correlación no es fiable (p.ej. usas auriculares, sin
    fuga de audio), devuelve 0 y no toca nada."""
    if not SCIPY_AVAILABLE:
        return 0
    n = int(window_sec * rate)
    a = reference[:n].astype(np.float64)
    b = other[:n].astype(np.float64)
    if len(a) < rate or len(b) < rate:
        return 0
    a = a - a.mean()
    b = b - b.mean()
    # downsample a ~2000 Hz para que la correlación sea rápida
    factor = max(int(rate // 2000), 1)
    a_ds = a[::factor]
    b_ds = b[::factor]
    if len(a_ds) < 10 or len(b_ds) < 10:
        return 0
    norm = (np.sqrt((a_ds ** 2).sum()) * np.sqrt((b_ds ** 2).sum())) + 1e-9
    corr = fftconvolve(a_ds, b_ds[::-1], mode="full") / norm
    mid = len(a_ds) - 1
    max_shift_ds = int(max_shift_sec * (rate / factor))
    lo = max(mid - max_shift_ds, 0)
    hi = min(mid + max_shift_ds + 1, len(corr))
    segment = corr[lo:hi]
    if len(segment) == 0:
        return 0
    best_idx = int(np.argmax(np.abs(segment)))
    peak = segment[best_idx]
    if abs(peak) < corr_threshold:
        return 0
    shift_ds = (lo + best_idx) - mid
    return int(round(shift_ds * factor))


def apply_shift(arr: np.ndarray, shift_samples: int) -> np.ndarray:
    """Desplaza 'arr' shift_samples hacia adelante (positivo) o lo recorta
    por delante (negativo)."""
    if shift_samples == 0:
        return arr
    if shift_samples > 0:
        return np.concatenate([np.zeros(shift_samples, dtype=np.float32), arr])
    return arr[-shift_samples:]


def mix_tracks_equal_len(a: np.ndarray, b: np.ndarray, gain_a: float, gain_b: float) -> np.ndarray:
    n = max(len(a), len(b))
    if len(a) < n:
        a = np.pad(a, (0, n - len(a)))
    if len(b) < n:
        b = np.pad(b, (0, n - len(b)))
    mixed = a * gain_a + b * gain_b
    return np.clip(mixed, -32768, 32767).astype(np.int16)


def save_wav_mono(path: str, samples_int16: np.ndarray, sample_rate: int):
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(FORMAT_WIDTH)
        wf.setframerate(sample_rate)
        wf.writeframes(samples_int16.tobytes())


def transcript_txt_path(directory: str, prefix: str) -> str:
    """Ruta del .txt de transcripción: <prefijo>_AAAA-MM-DD.txt en 'directory'.
    Si ya existe uno de ese día (otra reunión), añade _2, _3... para no pisarlo."""
    date = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(directory, f"{prefix}_{date}.txt")
    n = 2
    while os.path.exists(path):
        path = os.path.join(directory, f"{prefix}_{date}_{n}.txt")
        n += 1
    return path


def format_ts(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


# --------------------------------------------------------------------------
# Grabador dual (sistema + micrófono) con timestamps de reloj de pared
# --------------------------------------------------------------------------

class DualRecorder:
    """Graba en paralelo el loopback del sistema y el micrófono elegido,
    registrando timestamps reales de inicio/fin de cada pista para poder
    alinearlas correctamente al mezclar. Expone además el nivel RMS
    instantáneo de cada pista (para el visualizador de la UI)."""

    def __init__(self, mic_device_index=None):
        if pyaudio is None:
            raise RuntimeError(
                "PyAudioWPatch no está instalado o no estás en Windows. "
                "Instala con: pip install PyAudioWPatch"
            )
        self.p = pyaudio.PyAudio()
        self.loopback_info = self._get_default_loopback_device()
        self.mic_info = (
            self.p.get_device_info_by_index(mic_device_index)
            if mic_device_index is not None
            else self.p.get_default_input_device_info()
        )

        self._sys_frames = []
        self._mic_frames = []
        self._sys_timing = {}
        self._mic_timing = {}
        self._sys_error = {}
        self._mic_error = {}
        self._sys_level = {"v": 0.0}
        self._mic_level = {"v": 0.0}
        self._sys_stream = None
        self._mic_stream = None
        self._closed = False
        self._recording = False
        self._wall_start = None

    def _get_default_loopback_device(self):
        wasapi_info = self.p.get_host_api_info_by_type(pyaudio.paWASAPI)
        default_speakers = self.p.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
        if not default_speakers.get("isLoopbackDevice", False):
            for loopback in self.p.get_loopback_device_info_generator():
                if default_speakers["name"] in loopback["name"]:
                    return loopback
            raise RuntimeError(
                "No se encontró el dispositivo loopback correspondiente a la salida "
                "de audio por defecto. Activa 'Mezcla estéreo' en Windows o instala "
                "VB-Cable como alternativa."
            )
        return default_speakers

    def list_input_devices(self):
        devices = []
        for i in range(self.p.get_device_count()):
            info = self.p.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) > 0 and not info.get("isLoopbackDevice", False):
                devices.append((i, info["name"]))
        return devices

    def start(self):
        self._sys_frames = []
        self._mic_frames = []
        self._sys_timing = {}
        self._mic_timing = {}
        self._sys_error = {}
        self._mic_error = {}
        self._recording = True
        self._wall_start = time.time()

        # Capturamos con callbacks (no lectura bloqueante): WASAPI loopback
        # bloquea stream.read() cuando no suena nada, lo que dejaba hilos
        # colgados y provocaba un crash nativo al cerrar. Con callback,
        # PortAudio nos entrega los datos y el cierre es limpio y seguro.
        self._sys_stream = self._open_stream(
            self.loopback_info, self._sys_frames, self._sys_timing,
            self._sys_error, self._sys_level)
        self._mic_stream = self._open_stream(
            self.mic_info, self._mic_frames, self._mic_timing,
            self._mic_error, self._mic_level)

    def _open_stream(self, device_info, frame_list, timing, error_holder, level_holder):
        channels = min(int(device_info["maxInputChannels"]), 2) or 1
        rate = int(device_info["defaultSampleRate"])
        timing["channels"] = channels
        timing["nominal_rate"] = rate

        def callback(in_data, frame_count, time_info, status):
            frame_list.append(in_data)
            # Nivel RMS normalizado (0..1) con suavizado, para el visualizador.
            arr = np.frombuffer(in_data, dtype=np.int16)
            if arr.size:
                rms = float(np.sqrt(np.mean(arr.astype(np.float32) ** 2))) / 32768.0
                level_holder["v"] = level_holder["v"] * 0.6 + rms * 0.4
            return (None, pyaudio.paContinue)

        try:
            stream = self.p.open(
                format=pyaudio.paInt16,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=device_info["index"],
                frames_per_buffer=CHUNK,
                stream_callback=callback,
            )
        except Exception as e:
            error_holder["error"] = f"No se pudo abrir '{device_info['name']}': {e}"
            return None
        timing["start"] = time.perf_counter()
        return stream

    def elapsed_seconds(self):
        if not self._recording or self._wall_start is None:
            return 0
        return time.time() - self._wall_start

    def get_levels(self):
        """(nivel_sistema, nivel_micrófono) en 0..1, suavizados."""
        return self._sys_level["v"], self._mic_level["v"]

    def get_errors(self):
        errs = []
        if "error" in self._sys_error:
            errs.append("Audio del sistema: " + self._sys_error["error"])
        if "error" in self._mic_error:
            errs.append("Micrófono: " + self._mic_error["error"])
        return errs

    def stop_and_mix(self, target_rate: int = TARGET_RATE, fine_sync: bool = True):
        """Detiene la grabación, alinea temporalmente ambas pistas (offset de
        arranque + corrección de drift por reloj real + ajuste fino opcional
        por correlación cruzada) y devuelve (mixed, rate, sys_track, mic_track)."""
        # Marca de fin (reloj de pared) para ambos streams antes de pararlos.
        stop_ts = time.perf_counter()
        for stream, timing in ((self._sys_stream, self._sys_timing),
                               (self._mic_stream, self._mic_timing)):
            timing["stop"] = stop_ts
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
        self._sys_stream = None
        self._mic_stream = None
        self._recording = False

        sys_raw = b"".join(self._sys_frames)
        mic_raw = b"".join(self._mic_frames)
        sys_channels = self._sys_timing.get("channels", 2)
        mic_channels = self._mic_timing.get("channels", 1)

        sys_mono = bytes_to_float_mono(sys_raw, sys_channels)
        mic_mono = bytes_to_float_mono(mic_raw, mic_channels)

        # --- Resample por tasa NOMINAL del dispositivo ---
        # OJO: NO usamos "muestras/duración de pared" como tasa efectiva. WASAPI
        # loopback no entrega muestras durante los silencios (y el primer buffer
        # del callback llega con latencia), así que la cuenta de muestras del
        # sistema es menor que la duración de pared -> la tasa efectiva saldría
        # demasiado baja y el audio se ESTIRARÍA (se oye ralentizado y grave).
        # El drift real de reloj es <0.5% e imperceptible, así que la tasa
        # nominal es lo correcto y robusto aquí.
        sys_nom_rate = self._sys_timing.get("nominal_rate", target_rate)
        mic_nom_rate = self._mic_timing.get("nominal_rate", target_rate)

        sys_target = resample_linear(sys_mono, sys_nom_rate, target_rate)
        mic_target = resample_linear(mic_mono, mic_nom_rate, target_rate)

        # --- Alineación de offset de arranque entre los dos hilos ---
        sys_start = self._sys_timing.get("start")
        mic_start = self._mic_timing.get("start")
        if sys_start is not None and mic_start is not None:
            t0_ref = min(sys_start, mic_start)
            sys_target = pad_front(sys_target, int(round((sys_start - t0_ref) * target_rate)))
            mic_target = pad_front(mic_target, int(round((mic_start - t0_ref) * target_rate)))

        # --- Ajuste fino opcional por correlación cruzada (fuga del altavoz
        # captada por el micrófono). Se ignora si no es fiable. ---
        if fine_sync:
            shift = estimate_fine_shift(sys_target, mic_target, target_rate)
            if shift != 0:
                mic_target = apply_shift(mic_target, shift)

        mixed = mix_tracks_equal_len(sys_target, mic_target, gain_a=0.85, gain_b=1.0)
        sys_out = np.clip(sys_target, -32768, 32767).astype(np.int16)
        mic_out = np.clip(mic_target, -32768, 32767).astype(np.int16)

        return mixed, target_rate, sys_out, mic_out

    def close(self):
        # Idempotente: evitar un segundo terminate() sobre PortAudio ya cerrado.
        if self._closed:
            return
        self._closed = True
        for stream in (self._sys_stream, self._mic_stream):
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
        self._sys_stream = None
        self._mic_stream = None
        try:
            self.p.terminate()
        except Exception:
            pass


# --------------------------------------------------------------------------
# Motor de transcripción (faster-whisper) con caché de modelo y streaming
# --------------------------------------------------------------------------

class Transcriber:
    """Envuelve faster-whisper con:
      - caché del modelo (cargarlo es lo más lento; se reutiliza entre usos),
      - filtro VAD para saltar silencios,
      - callbacks de progreso y de segmento (para streaming en la UI),
      - modo multi-pista con etiquetas de hablante (Tú / Ellos)."""

    def __init__(self):
        self._models = {}
        self._lock = threading.Lock()

    def get_model(self, model_size: str):
        with self._lock:
            if model_size not in self._models:
                self._models[model_size] = WhisperModel(
                    model_size, device="cpu", compute_type="int8")
            return self._models[model_size]

    def transcribe_jobs(self, jobs, model_size, language=None,
                        on_segment=None, on_progress=None, on_phase=None):
        """jobs: lista de (etiqueta|None, ruta). Devuelve lista de segmentos
        (start, label, text) ordenada por tiempo. Los callbacks llegan desde
        este mismo hilo (el llamante decide cómo saltar al hilo de la UI)."""
        if on_phase:
            on_phase(f"Cargando modelo '{model_size}'...")
        model = self.get_model(model_size)

        kwargs = dict(
            language=language,
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=True,
        )
        if language == "es":
            kwargs["initial_prompt"] = (
                "Transcripción de una reunión de trabajo en español.")

        all_segs = []
        n = len(jobs)
        for idx, (label, path) in enumerate(jobs):
            if on_phase:
                name = os.path.basename(path)
                on_phase(f"Transcribiendo {name}" + (f" [{label}]" if label else "") + "...")
            segments, info = model.transcribe(path, **kwargs)
            duration = max(getattr(info, "duration", 0.0) or 0.0, 0.001)
            for seg in segments:
                text = seg.text.strip()
                # Descarta segmentos que el propio modelo considera no-habla
                # (típica fuente de alucinaciones en silencios).
                if not text or getattr(seg, "no_speech_prob", 0.0) > 0.85:
                    continue
                all_segs.append((seg.start, label, text))
                if on_segment:
                    on_segment(seg.start, label, text)
                if on_progress:
                    frac = (idx + min(seg.end / duration, 1.0)) / n
                    on_progress(frac)
            if on_progress:
                on_progress((idx + 1) / n)

        all_segs.sort(key=lambda s: s[0])
        return all_segs

    @staticmethod
    def format_segments(segs, with_timestamps=True):
        lines = []
        for start, label, text in segs:
            prefix = ""
            if with_timestamps:
                prefix += f"[{format_ts(start)}] "
            if label:
                prefix += f"{label}: "
            lines.append(prefix + text)
        return "\n".join(lines)


# --------------------------------------------------------------------------
# Detector de reuniones de Teams (heurístico, en segundo plano)
# --------------------------------------------------------------------------

def teams_pids():
    """PIDs de los procesos de Teams en ejecución (vacío si no hay)."""
    pids = set()
    if not PSUTIL_AVAILABLE:
        return pids
    for proc in psutil.process_iter(["name", "pid"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name in TEAMS_PROCESS_NAMES or "teams" in name:
                pids.add(proc.info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return pids


def teams_window_titles(pids):
    """Títulos de las ventanas visibles pertenecientes a los procesos dados."""
    titles = []
    if not WIN32_AVAILABLE or not pids:
        return titles

    def enum_handler(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return
        if pid not in pids:
            return
        title = win32gui.GetWindowText(hwnd)
        if title:
            titles.append(title)

    try:
        win32gui.EnumWindows(enum_handler, None)
    except Exception:
        pass
    return titles


def detect_meeting_prefix() -> str:
    """Prefijo para el nombre del .txt según el título de la reunión de Teams
    en curso (ver MEETING_NAME_RULES). Si no se puede determinar o no hay
    regla que aplique, devuelve el prefijo por defecto ('reunion')."""
    if not (WIN32_AVAILABLE and PSUTIL_AVAILABLE):
        return DEFAULT_TRANSCRIPT_PREFIX
    try:
        titles = teams_window_titles(teams_pids())
    except Exception:
        return DEFAULT_TRANSCRIPT_PREFIX
    for title in titles:
        title_l = title.lower()
        for substring, prefix in MEETING_NAME_RULES:
            if substring in title_l:
                return prefix
    return DEFAULT_TRANSCRIPT_PREFIX


class MeetingWatcher(threading.Thread):
    """Sondea periódicamente si Teams está en una reunión/llamada.

    Señal PRINCIPAL (fiable): que Teams esté usando el micrófono en ese
    momento, según el registro de Windows (CapabilityAccessManager). Teams
    toma el micro al entrar en una llamada y lo suelta al salir, así que es
    una señal mucho más robusta que el título de la ventana (que solo lleva el
    nombre de la reunión).

    Señal de RESPALDO (si no hay acceso al registro): proceso de Teams en
    ejecución + ventana visible cuyo título contiene una palabra clave. Menos
    fiable; solo se usa como último recurso."""

    def __init__(self, on_meeting_start, on_meeting_end=None,
                 keywords=None, poll_interval=4):
        super().__init__(daemon=True)
        self.on_meeting_start = on_meeting_start
        self.on_meeting_end = on_meeting_end
        # keywords: solo se usan en el método de respaldo por título de ventana.
        self.keywords = [k.strip().lower() for k in (keywords or DEFAULT_MEETING_KEYWORDS) if k.strip()]
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._in_meeting = False

    def stop(self):
        self._stop_event.set()

    def update_keywords(self, keywords):
        self.keywords = [k.strip().lower() for k in keywords if k.strip()]

    @staticmethod
    def _consent_teams_active(subpath):
        """True si alguna subclave cuyo nombre contiene 'teams' tiene
        LastUsedTimeStop == 0 (micrófono en uso ahora mismo)."""
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, subpath)
        except OSError:
            return False
        active = False
        with key:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(key, i)
                except OSError:
                    break
                i += 1
                if "teams" not in sub.lower():
                    continue
                try:
                    with winreg.OpenKey(key, sub) as sk:
                        stop = winreg.QueryValueEx(sk, "LastUsedTimeStop")[0]
                except OSError:
                    continue
                if stop == 0:
                    active = True
                    break
        return active

    def _teams_using_mic(self):
        """True/False si Teams está usando el micrófono ahora. Devuelve None si
        no se puede determinar por el registro (para caer al método de respaldo)."""
        if not WINREG_AVAILABLE:
            return None
        try:
            return (self._consent_teams_active(MIC_CONSENT_KEY)
                    or self._consent_teams_active(MIC_CONSENT_KEY + r"\NonPackaged"))
        except Exception:
            return None

    def _meeting_window_open(self, pids):
        """True si alguna ventana visible PERTENECIENTE A TEAMS tiene un título
        con palabra clave de reunión. Se restringe a ventanas de Teams para no
        dar falsos positivos con otras ventanas (p.ej. la propia app 'Max
        Recorder', un Word titulado 'meeting', etc.)."""
        for title in teams_window_titles(pids):
            title_l = title.lower()
            if any(k in title_l for k in self.keywords):
                return True
        return False

    def run(self):
        while not self._stop_event.is_set():
            try:
                mic = self._teams_using_mic()
                if mic is None:
                    # Sin registro: respaldo por título de ventana de Teams.
                    pids = teams_pids()
                    meeting_now = bool(pids) and self._meeting_window_open(pids)
                else:
                    # Señal fiable por micrófono. Confirmamos que Teams sigue vivo
                    # (evita una entrada obsoleta si Teams murió reteniendo el micro).
                    meeting_now = bool(mic) and bool(teams_pids())
            except Exception:
                meeting_now = False

            if meeting_now and not self._in_meeting:
                self._in_meeting = True
                self.on_meeting_start()
            elif not meeting_now and self._in_meeting:
                self._in_meeting = False
                if self.on_meeting_end:
                    self.on_meeting_end()

            self._stop_event.wait(self.poll_interval)


# --------------------------------------------------------------------------
# Popup de aviso estilo notificación (con animación de entrada)
# --------------------------------------------------------------------------

class MeetingPopup(tk.Toplevel):
    def __init__(self, master, on_accept, on_dismiss=None, timeout=30):
        super().__init__(master)
        self.on_accept = on_accept
        self.on_dismiss = on_dismiss
        self._closed = False

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.w, self.h = 350, 148
        sx, sy = self.winfo_screenwidth(), self.winfo_screenheight()
        self._final_x = sx - self.w - 24
        self._y = sy - self.h - 70
        # arranca fuera de pantalla (derecha) y entra deslizándose
        self._x = float(sx)
        self.geometry(f"{self.w}x{self.h}+{int(self._x)}+{self._y}")
        self.configure(bg=P.PANEL, highlightthickness=1, highlightbackground=P.ACCENT)

        head = tk.Frame(self, bg=P.PANEL)
        head.pack(fill="x", padx=14, pady=(12, 2))
        self._led = StatusLED(head, bg=P.PANEL)
        self._led.set_state("recording")
        self._led.pack(side="left", padx=(0, 8))
        tk.Label(head, text="REUNIÓN DE TEAMS DETECTADA",
                 bg=P.PANEL, fg=P.TEXT, font=("Consolas", 10, "bold"),
                 anchor="w").pack(side="left")

        tk.Label(self, text="¿Quieres iniciar la grabación ahora?",
                 bg=P.PANEL, fg=P.DIM, font=P.FONT, anchor="w",
                 justify="left").pack(fill="x", padx=14, pady=(2, 10))

        btns = tk.Frame(self, bg=P.PANEL)
        btns.pack(padx=14, pady=4, fill="x")
        TechButton(btns, kind="danger", text="●  GRABAR",
                   command=self._accept, width=11).pack(side="left", padx=(0, 8))
        TechButton(btns, kind="ghost", text="IGNORAR",
                   command=self._dismiss, width=11).pack(side="left")

        self._slide_in()
        self.after(timeout * 1000, self._auto_dismiss)

    def _slide_in(self):
        if self._closed:
            return
        dist = self._x - self._final_x
        if dist <= 1:
            self._x = self._final_x
        else:
            self._x -= max(dist * 0.25, 2)
        self.geometry(f"{self.w}x{self.h}+{int(self._x)}+{self._y}")
        if self._x > self._final_x:
            self.after(15, self._slide_in)

    def _accept(self):
        self._close()
        self.on_accept()

    def _dismiss(self):
        self._close()
        if self.on_dismiss:
            self.on_dismiss()

    def _auto_dismiss(self):
        if not self._closed:
            self._dismiss()

    def _close(self):
        if not self._closed:
            self._closed = True
            self.destroy()


# --------------------------------------------------------------------------
# Ventana de ajustes
# --------------------------------------------------------------------------

class SettingsWindow(tk.Toplevel):
    """Ajustes de la app: carpeta por defecto de transcripciones y opciones de
    segundo plano (detección de reuniones, palabras clave, sondeo, arranque al
    iniciar Windows, probar el aviso). Se guardan en config.json al cerrar."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Ajustes — Max Recorder")
        self.configure(bg=P.BG)
        self.resizable(False, False)
        self.transient(app)
        self.geometry(f"+{app.winfo_rootx() + 90}+{app.winfo_rooty() + 70}")
        self.protocol("WM_DELETE_WINDOW", self._save_close)

        tk.Label(self, text="⚙ AJUSTES", bg=P.BG, fg=P.ACCENT,
                 font=("Consolas", 12, "bold"), anchor="w").pack(
            fill="x", padx=12, pady=(10, 0))

        # ---- Transcripciones ----
        _, tr = make_section(self, "Transcripciones")
        row = tk.Frame(tr, bg=P.PANEL)
        row.pack(fill="x", pady=2)
        dark_label(row, text="Carpeta por defecto:").pack(side="left", padx=(2, 4))
        dark_entry(row, textvariable=app.transcript_dir, width=45).pack(
            side="left", padx=4, fill="x", expand=True, ipady=3)
        TechButton(row, text="ELEGIR...", command=self._choose_dir).pack(side="left", padx=4)

        # ---- Segundo plano ----
        _, bg_sec = make_section(self, "Segundo plano · Detección de reuniones")
        row1 = tk.Frame(bg_sec, bg=P.PANEL)
        row1.pack(fill="x", pady=2)
        dark_check(row1, text="Detectar reuniones automáticamente y avisar (siempre activa)",
                   variable=app.auto_detect_var, state="disabled",
                   disabledforeground=P.TEXT).pack(side="left")
        dark_label(row1, text="Sondeo (s):").pack(side="left", padx=(16, 4))
        tk.Spinbox(row1, from_=2, to=30, width=4, textvariable=app.poll_interval_var,
                   bg=P.FIELD, fg=P.TEXT, buttonbackground=P.PANEL2,
                   insertbackground=P.ACCENT, relief="flat",
                   highlightthickness=1, highlightbackground=P.BORDER).pack(side="left")

        row2 = tk.Frame(bg_sec, bg=P.PANEL)
        row2.pack(fill="x", pady=2)
        dark_label(row2, text="Palabras clave (respaldo por título):").pack(side="left", padx=(2, 4))
        dark_entry(row2, textvariable=app.keywords_var).pack(
            side="left", padx=4, fill="x", expand=True, ipady=3)

        row3 = tk.Frame(bg_sec, bg=P.PANEL)
        row3.pack(fill="x", pady=2)
        dark_check(row3,
                   text="Arrancar automáticamente al iniciar sesión de Windows (en segundo plano)",
                   variable=app.autostart_var, command=app._toggle_autostart).pack(side="left")
        if not WINREG_AVAILABLE:
            lbl_no_reg = dark_label(row3, text="(no disponible en esta plataforma)")
            lbl_no_reg.config(fg=P.RED)
            lbl_no_reg.pack(side="left", padx=6)

        row4 = tk.Frame(bg_sec, bg=P.PANEL)
        row4.pack(fill="x", pady=(4, 2))
        TechButton(row4, text="PROBAR AVISO", command=app._test_popup).pack(side="left", padx=2)

        if not (PSUTIL_AVAILABLE and WIN32_AVAILABLE):
            tk.Label(bg_sec, text="⚠ Faltan dependencias para la detección: pip install psutil pywin32",
                     bg=P.PANEL, fg=P.AMBER, font=P.FONT_SM, anchor="w").pack(fill="x")
        if not TRAY_AVAILABLE:
            tk.Label(bg_sec, text="⚠ Para el segundo plano instala: pip install pystray pillow",
                     bg=P.PANEL, fg=P.AMBER, font=P.FONT_SM, anchor="w").pack(fill="x")

        # ---- Cierre ----
        btns = tk.Frame(self, bg=P.BG)
        btns.pack(fill="x", padx=12, pady=10)
        TechButton(btns, kind="primary", text="GUARDAR Y CERRAR",
                   command=self._save_close).pack(side="right")

    def _choose_dir(self):
        d = filedialog.askdirectory(
            initialdir=self.app.transcript_dir.get() or self.app.output_dir.get(),
            parent=self)
        if d:
            self.app.transcript_dir.set(d)

    def _save_close(self):
        self.app._apply_settings()
        self.destroy()


# --------------------------------------------------------------------------
# GUI principal
# --------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self, start_in_tray=False):
        super().__init__()
        self.title("Max Recorder — Transcripción de reuniones de Teams")
        self.geometry("920x640")
        self.minsize(760, 520)
        self.configure(bg=P.BG)

        self.recorder = None
        self.recording = False
        self.transcribing = False
        self.last_paths = None       # dict(mixed=, sys=, mic=) de la última grabación
        self.transcript_text = ""
        self.meeting_watcher = None
        self.tray_icon = None
        self._quit_after_save = False
        self._rec_blink = False
        self.transcriber = Transcriber()

        cfg = load_config()
        self.output_dir = tk.StringVar(value=cfg.get("output_dir", OUTPUT_DIR_DEFAULT))
        self.transcript_dir = tk.StringVar(value=cfg.get("transcript_dir", TRANSCRIPT_DIR_DEFAULT))
        # La detección de reuniones va siempre activa: se arranca sola al
        # abrir la app (haya o no modo bandeja) y la casilla se deshabilita
        # para que no se pueda dejar desactivada por error.
        self.auto_detect_var = tk.BooleanVar(value=True)
        self.keywords_var = tk.StringVar(
            value=cfg.get("keywords", ", ".join(DEFAULT_MEETING_KEYWORDS)))
        try:
            poll = int(cfg.get("poll_interval", 4))
        except (TypeError, ValueError):
            poll = 4
        self.poll_interval_var = tk.IntVar(value=min(max(poll, 2), 30))
        self.autostart_var = tk.BooleanVar(value=is_autostart_enabled())
        self.settings_window = None
        self.speakers_var = tk.BooleanVar(value=True)     # modo Tú/Ellos
        self.timestamps_var = tk.BooleanVar(value=True)   # marcas de tiempo
        self.auto_transcribe_var = tk.BooleanVar(value=True)

        self._setup_style()
        self._build_ui()
        self._refresh_mic_list()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if TRAY_AVAILABLE:
            self._setup_tray()

        # La detección arranca siempre, independientemente del modo bandeja.
        self.after(0, self._toggle_auto_detect)

        # Arranque en modo bandeja (inicio automático al iniciar sesión):
        # ocultar la ventana.
        if start_in_tray:
            self.after(0, self._start_in_tray)

    def _start_in_tray(self):
        if TRAY_AVAILABLE:
            self.withdraw()

    # ---------------- Estilo ----------------

    def _setup_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TCombobox",
                        fieldbackground=P.FIELD, background=P.PANEL2,
                        foreground=P.TEXT, arrowcolor=P.ACCENT,
                        bordercolor=P.BORDER, lightcolor=P.PANEL,
                        darkcolor=P.PANEL, selectbackground=P.ACCENT_DK,
                        selectforeground=P.TEXT)
        style.map("TCombobox",
                  fieldbackground=[("readonly", P.FIELD)],
                  foreground=[("readonly", P.TEXT)])
        # desplegable del combobox
        self.option_add("*TCombobox*Listbox.background", P.FIELD)
        self.option_add("*TCombobox*Listbox.foreground", P.TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", P.ACCENT_DK)
        self.option_add("*TCombobox*Listbox.selectForeground", P.TEXT)
        style.configure("Vertical.TScrollbar",
                        background=P.PANEL2, troughcolor=P.FIELD,
                        bordercolor=P.BORDER, arrowcolor=P.DIM)

    # ---------------- Construcción de la UI ----------------

    def _build_ui(self):
        # ---- Cabecera ----
        header = tk.Frame(self, bg=P.BG)
        header.pack(fill="x", padx=10, pady=(10, 0))
        tk.Label(header, text="◉ MAX RECORDER", bg=P.BG, fg=P.ACCENT,
                 font=P.TITLE, anchor="w").pack(side="left")
        # Botones de la esquina superior derecha: pasar a segundo plano
        # (antes "Bandeja") y abrir la ventana de ajustes.
        TechButton(header, text="▾ SEGUNDO PLANO",
                   command=self._minimize_to_tray).pack(side="right")
        TechButton(header, text="⚙ AJUSTES",
                   command=self._open_settings).pack(side="right", padx=6)
        led_box = tk.Frame(header, bg=P.BG)
        led_box.pack(side="right", padx=(0, 8))
        self.led = StatusLED(led_box, bg=P.BG)
        self.led.set_state("ready")
        self.led.pack(side="left", padx=(0, 6))
        self.lbl_status = tk.Label(led_box, text="LISTO", bg=P.BG, fg=P.DIM,
                                   font=("Consolas", 9))
        self.lbl_status.pack(side="left")

        # ---- Visualizador ----
        self.visualizer = AudioVisualizer(self)
        self.visualizer.pack(fill="x", padx=10, pady=(8, 0))
        self.visualizer.level_source = self._get_levels

        # ---- Grabación ----
        _, rec = make_section(self, "Grabación")
        row = tk.Frame(rec, bg=P.PANEL)
        row.pack(fill="x", pady=2)
        self.btn_start = TechButton(row, kind="primary", text="●  INICIAR",
                                    command=self._start_recording, width=13)
        self.btn_start.pack(side="left", padx=(2, 6))
        self.btn_stop = TechButton(row, kind="danger", text="■  DETENER",
                                   command=self._stop_recording, width=13,
                                   state="disabled")
        self.btn_stop.pack(side="left", padx=6)
        self.lbl_rec = tk.Label(row, text="", bg=P.PANEL, fg=P.RED,
                                font=("Consolas", 10, "bold"), width=6)
        self.lbl_rec.pack(side="left", padx=(14, 0))
        self.lbl_timer = tk.Label(row, text="00:00:00", bg=P.PANEL, fg=P.TEXT,
                                  font=P.MONO_BIG)
        self.lbl_timer.pack(side="left", padx=6)

        row2 = tk.Frame(rec, bg=P.PANEL)
        row2.pack(fill="x", pady=(6, 2))
        dark_label(row2, text="Micrófono:").pack(side="left", padx=(2, 4))
        self.mic_combo = ttk.Combobox(row2, state="readonly", width=44)
        self.mic_combo.pack(side="left", padx=4, fill="x", expand=True)
        TechButton(row2, text="⟳", command=self._refresh_mic_list, width=3).pack(side="left", padx=4)
        dark_label(row2, dim=True,
                   text="(el audio del sistema se captura solo, vía WASAPI loopback)").pack(
            side="left", padx=6)

        row3 = tk.Frame(rec, bg=P.PANEL)
        row3.pack(fill="x", pady=(4, 2))
        dark_label(row3, text="Carpeta de salida:").pack(side="left", padx=(2, 4))
        dark_entry(row3, textvariable=self.output_dir).pack(
            side="left", padx=4, fill="x", expand=True, ipady=3)
        TechButton(row3, text="ELEGIR...", command=self._choose_output_dir).pack(
            side="left", padx=4)

        # (Los ajustes de segundo plano — detección, palabras clave, arranque
        # con Windows, probar aviso — viven ahora en la ventana de Ajustes.)

        # ---- Transcripción ----
        outer_tr, tr = make_section(self, "Transcripción · faster-whisper (local)")
        outer_tr.pack_configure(fill="both", expand=True, pady=(8, 10))

        ctr = tk.Frame(tr, bg=P.PANEL)
        ctr.pack(fill="x", pady=2)
        dark_label(ctr, text="Modelo:").pack(side="left", padx=(2, 4))
        self.whisper_model_combo = ttk.Combobox(
            ctr, state="readonly", width=10,
            values=["tiny", "base", "small", "medium", "large-v3"])
        self.whisper_model_combo.set("small")
        self.whisper_model_combo.pack(side="left", padx=4)
        dark_label(ctr, text="Idioma:").pack(side="left", padx=(10, 4))
        self.lang_entry = dark_entry(ctr, width=5)
        self.lang_entry.insert(0, "es")
        self.lang_entry.pack(side="left", padx=4, ipady=3)
        dark_check(ctr, text="Tú / Ellos", variable=self.speakers_var).pack(side="left", padx=(12, 0))
        dark_check(ctr, text="Marcas de tiempo", variable=self.timestamps_var).pack(side="left", padx=(8, 0))
        dark_check(ctr, text="Auto al detener", variable=self.auto_transcribe_var).pack(side="left", padx=(8, 0))

        ctr2 = tk.Frame(tr, bg=P.PANEL)
        ctr2.pack(fill="x", pady=(4, 2))
        self.btn_transcribe = TechButton(
            ctr2, kind="primary", text="▶  TRANSCRIBIR ÚLTIMA",
            command=self._transcribe, state="disabled")
        self.btn_transcribe.pack(side="left", padx=2)
        TechButton(ctr2, text="ARCHIVO...", command=self._transcribe_file).pack(side="left", padx=6)
        TechButton(ctr2, text="GUARDAR .TXT", command=self._save_transcript).pack(side="left", padx=6)
        self.lbl_tr_status = dark_label(ctr2, dim=True, text="")
        self.lbl_tr_status.pack(side="left", padx=10)

        self.progress = TechProgress(tr)
        self.progress.pack(fill="x", pady=(6, 4))

        txt_frame = tk.Frame(tr, bg=P.PANEL)
        txt_frame.pack(fill="both", expand=True)
        self.txt_transcript = tk.Text(
            txt_frame, height=10, wrap="word", bg=P.FIELD, fg=P.TEXT,
            insertbackground=P.ACCENT, relief="flat", font=("Consolas", 10),
            highlightthickness=1, highlightbackground=P.BORDER,
            selectbackground=P.ACCENT_DK, padx=8, pady=6)
        sb = ttk.Scrollbar(txt_frame, orient="vertical", command=self.txt_transcript.yview)
        self.txt_transcript.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt_transcript.pack(side="left", fill="both", expand=True)
        # colores para las etiquetas de hablante y timestamps
        self.txt_transcript.tag_configure("ts", foreground=P.DIM)
        self.txt_transcript.tag_configure("me", foreground=P.GREEN)
        self.txt_transcript.tag_configure("them", foreground=P.ACCENT)


    def _set_status(self, text, led_state=None):
        # Corto: comparte la cabecera con el subtítulo y los botones.
        self.lbl_status.config(text=text.upper()[:34])
        if led_state:
            self.led.set_state(led_state)

    def _get_levels(self):
        if self.recorder is not None and self.recording:
            return self.recorder.get_levels()
        return 0.0, 0.0

    # ---------------- Carpeta / dispositivos ----------------

    def _choose_output_dir(self):
        d = filedialog.askdirectory()
        if d:
            self.output_dir.set(d)

    def _refresh_mic_list(self):
        if pyaudio is None:
            self.mic_combo["values"] = ["PyAudioWPatch no instalado"]
            return
        try:
            p = pyaudio.PyAudio()
            devices = []
            for i in range(p.get_device_count()):
                info = p.get_device_info_by_index(i)
                if info.get("maxInputChannels", 0) > 0 and not info.get("isLoopbackDevice", False):
                    devices.append(f"{i}: {info['name']}")
            p.terminate()
            self.mic_combo["values"] = devices
            if devices:
                self.mic_combo.current(0)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudieron listar dispositivos:\n{e}")

    # ---------------- Grabación ----------------

    def _start_recording(self):
        if self.recording:
            return
        if pyaudio is None:
            messagebox.showerror(
                "Falta dependencia",
                "Instala PyAudioWPatch (solo Windows):\npip install PyAudioWPatch")
            return
        mic_sel = self.mic_combo.get()
        mic_index = int(mic_sel.split(":")[0]) if mic_sel and ":" in mic_sel else None
        try:
            self.recorder = DualRecorder(mic_device_index=mic_index)
            self.recorder.start()
        except Exception as e:
            messagebox.showerror("Error al iniciar grabación", str(e))
            return

        self.recording = True
        # El nombre del .txt depende de la reunión en curso: se mira el título
        # de las ventanas de Teams AHORA (al detener podría estar ya cerrada).
        self._meeting_prefix = detect_meeting_prefix()
        self.visualizer.recording = True
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.btn_transcribe.config(state="disabled")
        self._set_status("Grabando sistema + micrófono", "recording")
        self.deiconify()
        self._tick_timer()
        self._blink_rec()

    def _blink_rec(self):
        if not self.recording:
            self.lbl_rec.config(text="")
            return
        self._rec_blink = not self._rec_blink
        self.lbl_rec.config(text="● REC" if self._rec_blink else "  REC")
        self.after(600, self._blink_rec)

    def _tick_timer(self):
        if not self.recording or self.recorder is None:
            return
        errors = self.recorder.get_errors()
        if errors:
            messagebox.showerror("Error durante la grabación", "\n".join(errors))
            self._stop_recording()
            return
        secs = int(self.recorder.elapsed_seconds())
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        self.lbl_timer.config(text=f"{h:02d}:{m:02d}:{s:02d}")
        self.after(500, self._tick_timer)

    def _stop_recording(self):
        if not self.recorder or not self.recording:
            return
        # Detenemos el timer y deshabilitamos el botón inmediatamente. El
        # procesado (alineado + mezcla + guardado en disco) es pesado y puede
        # tardar bastante en grabaciones largas, así que lo hacemos en un hilo
        # aparte para no congelar (ni tumbar) la interfaz.
        self.recording = False
        self.visualizer.recording = False
        self.btn_stop.config(state="disabled")
        self._set_status("Procesando y alineando pistas...", "busy")
        output_dir = self.output_dir.get()
        transcript_dir = self.transcript_dir.get().strip() or output_dir
        prefix = getattr(self, "_meeting_prefix", DEFAULT_TRANSCRIPT_PREFIX)
        recorder = self.recorder
        self.recorder = None
        threading.Thread(
            target=self._stop_worker,
            args=(recorder, output_dir, transcript_dir, prefix), daemon=True).start()

    def _stop_worker(self, recorder, output_dir, transcript_dir, prefix):
        try:
            mixed, rate, sys_only, mic_only = recorder.stop_and_mix()
            errors = recorder.get_errors()
            recorder.close()

            os.makedirs(output_dir, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            paths = {
                "mixed": os.path.join(output_dir, f"reunion_{stamp}.wav"),
                "sys": os.path.join(output_dir, f"reunion_{stamp}_sistema.wav"),
                "mic": os.path.join(output_dir, f"reunion_{stamp}_mic.wav"),
                "txt": transcript_txt_path(transcript_dir, prefix),
            }

            save_wav_mono(paths["mixed"], mixed, rate)
            save_wav_mono(paths["sys"], sys_only, rate)
            save_wav_mono(paths["mic"], mic_only, rate)

            self.after(0, lambda: self._on_stop_done(paths, errors))
        except Exception as e:
            try:
                recorder.close()
            except Exception:
                pass
            self.after(0, lambda: self._on_stop_error(e))

    def _on_stop_done(self, paths, errors):
        self.btn_start.config(state="normal")
        self.btn_transcribe.config(state="normal")
        self.last_paths = paths
        self._set_status(f"Guardado: {os.path.basename(paths['mixed'])}", "ready")
        if errors:
            messagebox.showwarning("Avisos durante la grabación", "\n".join(errors))
        if self._quit_after_save:
            self._quit_after_save = False
            self._quit_app()
            return
        if self.auto_transcribe_var.get() and WHISPER_AVAILABLE:
            self._transcribe()

    def _on_stop_error(self, error):
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self._set_status("Error al procesar la grabación", "idle")
        messagebox.showerror("Error al detener la grabación", str(error))
        if self._quit_after_save:
            # Falló el guardado; preguntamos si aun así quiere cerrar.
            self._quit_after_save = False
            if messagebox.askyesno("Cerrar", "No se pudo guardar la grabación.\n¿Cerrar de todos modos?"):
                self._quit_app()

    # ---------------- Detección de reuniones ----------------

    def _toggle_auto_detect(self):
        if self.auto_detect_var.get():
            if not (PSUTIL_AVAILABLE and WIN32_AVAILABLE):
                # La detección es siempre activa por diseño; si faltan
                # dependencias solo lo reflejamos en el estado (el aviso
                # ⚠ ya está visible en la sección de segundo plano) en vez
                # de interrumpir el arranque con un diálogo.
                self.auto_detect_var.set(False)
                self._set_status("Detección no disponible (faltan dependencias)", "idle")
                return
            keywords = [k for k in self.keywords_var.get().split(",")]
            self.meeting_watcher = MeetingWatcher(
                on_meeting_start=self._on_meeting_detected,
                on_meeting_end=None,
                keywords=keywords,
                poll_interval=self.poll_interval_var.get(),
            )
            self.meeting_watcher.start()
            self._set_status("Vigilando Teams...", "watching")
        else:
            if self.meeting_watcher:
                self.meeting_watcher.stop()
                self.meeting_watcher = None
            self._set_status("Detección desactivada", "ready")

    def _on_meeting_detected(self):
        # Llamado desde el hilo del watcher: hay que saltar al hilo de Tkinter.
        self.after(0, self._show_meeting_popup)

    def _show_meeting_popup(self):
        if self.recording:
            return  # ya está grabando, no molestar
        MeetingPopup(self, on_accept=self._start_recording)

    def _test_popup(self):
        self._show_meeting_popup()

    # ---------------- Ajustes ----------------

    def _open_settings(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.lift()
            self.settings_window.focus_set()
            return
        self.settings_window = SettingsWindow(self)

    def _apply_settings(self):
        """Persiste los ajustes y los aplica al watcher en marcha."""
        try:
            poll = min(max(int(self.poll_interval_var.get()), 2), 30)
        except (tk.TclError, ValueError):
            poll = 4
        self.poll_interval_var.set(poll)
        save_config({
            "output_dir": self.output_dir.get().strip(),
            "transcript_dir": self.transcript_dir.get().strip(),
            "keywords": self.keywords_var.get(),
            "poll_interval": poll,
        })
        if self.meeting_watcher:
            self.meeting_watcher.update_keywords(self.keywords_var.get().split(","))
            self.meeting_watcher.poll_interval = poll

    # ---------------- Inicio automático ----------------

    def _toggle_autostart(self):
        if not WINREG_AVAILABLE:
            messagebox.showerror("No disponible", "El inicio automático solo está disponible en Windows.")
            self.autostart_var.set(False)
            return
        try:
            if self.autostart_var.get():
                enable_autostart()
                self._set_status("Inicio automático activado", "ready")
            else:
                disable_autostart()
                self._set_status("Inicio automático desactivado", "ready")
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo cambiar el inicio automático:\n{e}")
            self.autostart_var.set(is_autostart_enabled())

    # ---------------- Bandeja del sistema ----------------

    def _setup_tray(self):
        image = self._make_tray_image()
        menu = pystray.Menu(
            pystray.MenuItem("Abrir", self._tray_open, default=True),
            pystray.MenuItem("Iniciar grabación", lambda: self.after(0, self._start_recording)),
            pystray.MenuItem("Detener grabación", lambda: self.after(0, self._stop_recording)),
            pystray.MenuItem("Salir", self._tray_quit),
        )
        self.tray_icon = pystray.Icon("max_recorder", image, "Max Recorder", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _make_tray_image(self):
        img = Image.new("RGB", (64, 64), "#0a0e13")
        d = ImageDraw.Draw(img)
        d.ellipse((8, 8, 56, 56), outline="#00e5ff", width=4)
        d.ellipse((22, 22, 42, 42), fill="#ff3860")
        return img

    def _tray_open(self, icon=None, item=None):
        self.after(0, self.deiconify)

    def _tray_quit(self, icon=None, item=None):
        self.after(0, self._quit_app)

    def _minimize_to_tray(self):
        if not TRAY_AVAILABLE:
            messagebox.showerror("Falta dependencia", "Instala: pip install pystray pillow")
            return
        self.withdraw()

    def _on_close(self):
        if TRAY_AVAILABLE:
            if messagebox.askyesno(
                    "Minimizar",
                    "¿Minimizar a la bandeja del sistema y seguir vigilando reuniones?\n"
                    "(No = cerrar la aplicación por completo)"):
                self.withdraw()
                return
        self._quit_app()

    def _quit_app(self):
        # Aviso si hay una grabación en curso: evita perderla al cerrar.
        if self.recording:
            resp = messagebox.askyesnocancel(
                "Grabación en curso",
                "Hay una grabación en curso.\n\n"
                "• Sí: detenerla y guardarla antes de salir.\n"
                "• No: descartarla y salir (se pierde).\n"
                "• Cancelar: no cerrar.",
                icon="warning")
            if resp is None:
                return  # Cancelar: no cerramos
            if resp:
                # Guardar y salir: detenemos (guarda en segundo plano) y cerramos
                # cuando termine, desde _on_stop_done / _on_stop_error.
                self._quit_after_save = True
                self._set_status("Guardando antes de salir...", "busy")
                self._stop_recording()
                return
            # No: descartar la grabación en curso sin guardar.
            self.recording = False
            if self.recorder:
                try:
                    self.recorder.close()
                except Exception:
                    pass
                self.recorder = None

        self._apply_settings()
        if self.meeting_watcher:
            self.meeting_watcher.stop()
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        self.destroy()

    # ---------------- Transcripción ----------------

    def _transcribe(self):
        """Transcribe la última grabación. Si está activado 'Tú / Ellos' y
        existen las pistas separadas, transcribe cada una y entrelaza los
        segmentos con etiqueta de hablante."""
        if not self._check_whisper():
            return
        if not self.last_paths or not os.path.exists(self.last_paths["mixed"]):
            messagebox.showwarning("Aviso", "Primero graba y detén una grabación.")
            return

        jobs = None
        if self.speakers_var.get():
            sys_p, mic_p = self.last_paths.get("sys"), self.last_paths.get("mic")
            if sys_p and mic_p and os.path.exists(sys_p) and os.path.exists(mic_p):
                jobs = [("Ellos", sys_p), ("Tú", mic_p)]
        if jobs is None:
            jobs = [(None, self.last_paths["mixed"])]

        # Ruta calculada al detener la grabación (reunion_/weekly_AAAA-MM-DD.txt
        # en la carpeta de transcripciones); re-transcribir sobreescribe el mismo.
        autosave = self.last_paths.get("txt")
        if not autosave:
            autosave = transcript_txt_path(
                self.transcript_dir.get().strip() or self.output_dir.get(),
                DEFAULT_TRANSCRIPT_PREFIX)
            self.last_paths["txt"] = autosave
        self._run_transcription(jobs, autosave_path=autosave)

    def _transcribe_file(self):
        """Transcribe un archivo de audio cualquiera elegido por el usuario."""
        if not self._check_whisper():
            return
        path = filedialog.askopenfilename(
            initialdir=self.output_dir.get(),
            filetypes=[("Audio", "*.wav *.mp3 *.m4a *.flac *.ogg *.opus"), ("Todos", "*.*")])
        if not path:
            return
        base = os.path.splitext(path)[0]
        self._run_transcription([(None, path)], autosave_path=base + "_transcripcion.txt")

    def _check_whisper(self):
        if not WHISPER_AVAILABLE:
            messagebox.showerror(
                "Falta dependencia",
                "Instala faster-whisper:\npip install faster-whisper")
            return False
        if self.transcribing:
            messagebox.showinfo("Transcripción", "Ya hay una transcripción en curso.")
            return False
        return True

    def _run_transcription(self, jobs, autosave_path=None):
        self.transcribing = True
        self.btn_transcribe.config(state="disabled")
        self.txt_transcript.delete("1.0", tk.END)
        self.progress.set(None)  # indeterminada mientras carga el modelo
        self._set_status("Transcribiendo...", "busy")
        model_size = self.whisper_model_combo.get()
        lang = self.lang_entry.get().strip() or None
        threading.Thread(
            target=self._transcribe_worker,
            args=(jobs, model_size, lang, autosave_path), daemon=True).start()

    def _transcribe_worker(self, jobs, model_size, lang, autosave_path):
        try:
            segs = self.transcriber.transcribe_jobs(
                jobs, model_size, language=lang,
                on_segment=lambda s, l, t: self.after(0, self._append_segment, s, l, t),
                on_progress=lambda f: self.after(0, self.progress.set, f),
                on_phase=lambda msg: self.after(0, self.lbl_tr_status.config, {"text": msg}),
            )
            multi = len(jobs) > 1
            text = self.transcriber.format_segments(
                segs, with_timestamps=self.timestamps_var.get())
            self.transcript_text = text

            saved = None
            if autosave_path and text.strip():
                try:
                    os.makedirs(os.path.dirname(autosave_path) or ".", exist_ok=True)
                    with open(autosave_path, "w", encoding="utf-8") as f:
                        f.write(text)
                    saved = autosave_path
                except OSError:
                    pass

            self.after(0, lambda: self._on_transcribe_done(segs, multi, saved))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error en transcripción", str(e)))
            self.after(0, lambda: self._set_status("Error al transcribir", "idle"))
            self.after(0, self.progress.hide)
        finally:
            self.transcribing = False
            self.after(0, lambda: self.btn_transcribe.config(
                state="normal" if self.last_paths else "disabled"))

    def _append_segment(self, start, label, text):
        """Streaming: añade un segmento al panel según se va transcribiendo."""
        if self.timestamps_var.get():
            self.txt_transcript.insert(tk.END, f"[{format_ts(start)}] ", "ts")
        if label:
            tag = "me" if label == "Tú" else "them"
            self.txt_transcript.insert(tk.END, f"{label}: ", tag)
        self.txt_transcript.insert(tk.END, text + "\n")
        self.txt_transcript.see(tk.END)

    def _on_transcribe_done(self, segs, multi, saved_path):
        # Con varias pistas los segmentos llegaron intercalados por pista;
        # reescribimos el panel ya ordenado por tiempo.
        if multi:
            self.txt_transcript.delete("1.0", tk.END)
            for start, label, text in segs:
                self._append_segment(start, label, text)
        self.progress.set(1.0)
        n = len(segs)
        msg = f"{n} segmentos"
        if saved_path:
            msg += f" · guardado {os.path.basename(saved_path)}"
        self.lbl_tr_status.config(text=msg)
        self._set_status("Transcripción completada", "ready")

    def _save_transcript(self):
        text = self.txt_transcript.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("Aviso", "No hay transcripción que guardar.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt", initialdir=self.output_dir.get(), initialfile="transcripcion.txt")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)


if __name__ == "__main__":
    if sys.platform != "win32":
        print("Aviso: esta herramienta usa WASAPI loopback y solo funciona en Windows.")

    # Si vuelve a producirse un cierre inesperado (crash nativo), este log
    # guardará la traza C/Python exacta para diagnosticarlo.
    try:
        _crash_log = open(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "crash.log"),
            "a", buffering=1)
        faulthandler.enable(file=_crash_log)
    except Exception:
        faulthandler.enable()

    parser = argparse.ArgumentParser(description="Max Recorder — Grabador de reuniones de Teams")
    parser.add_argument(
        "--tray", action="store_true",
        help="Arrancar minimizado en la bandeja con la detección de reuniones activada "
             "(lo usa el inicio automático al iniciar sesión).")
    args = parser.parse_args()

    app = App(start_in_tray=args.tray)
    app.mainloop()
