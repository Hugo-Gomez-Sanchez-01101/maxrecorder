"""
Grabador de Reuniones de Teams — Sistema + Micrófono y Transcripción
====================================================================

Graba simultáneamente:
  - El audio de salida de Windows (lo que suena por los altavoces/lo que dicen los
    demás en Teams) vía WASAPI loopback.
  - Tu micrófono.
Mezcla ambas pistas en un único WAV con alineación temporal correcta (corrige el
offset de arranque entre hilos y el drift de reloj entre dispositivos) y transcribe
con faster-whisper (local, gratis). El resumen de las transcripciones se genera
aparte mediante una tarea programada de Claude, no desde esta app.

Además puede quedarse en segundo plano (bandeja del sistema) y detectar cuándo
arranca una reunión de Teams para mostrar un aviso y ofrecer grabar con un clic.
Puede arrancar automáticamente al iniciar sesión (minimizada a la bandeja) para
no tener que lanzarla a mano; ver los botones de "inicio automático" o el
argumento --tray.

Solo funciona en Windows (usa PyAudioWPatch / WASAPI loopback).
"""

import os
import sys
import time
import wave
import argparse
import threading
import faulthandler
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
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
OUTPUT_DIR_DEFAULT = os.path.join(os.path.expanduser("~"), "Documents", "GrabacionesRecMax")

TEAMS_PROCESS_NAMES = {"teams.exe", "ms-teams.exe"}
DEFAULT_MEETING_KEYWORDS = ["reunión", "reunion", "meeting", "llamada", "call"]

# Detección de "en reunión" por uso del micrófono (fiable, sin depender del
# título de ventana). Windows registra aquí, por app, cuándo empezó/terminó de
# usar el micro; si LastUsedTimeStop == 0, la app lo está usando AHORA mismo.
MIC_CONSENT_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
    r"\ConsentStore\microphone"
)

# Clave de arranque automático al iniciar sesión (por usuario, sin admin)
AUTOSTART_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_VALUE_NAME = "GrabadorTeams"


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
            winreg.DeleteValue(key, AUTOSTART_VALUE_NAME)
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


# --------------------------------------------------------------------------
# Grabador dual (sistema + micrófono) con timestamps de reloj de pared
# --------------------------------------------------------------------------

class DualRecorder:
    """Graba en paralelo el loopback del sistema y el micrófono elegido,
    registrando timestamps reales de inicio/fin de cada pista para poder
    alinearlas correctamente al mezclar."""

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
            self.loopback_info, self._sys_frames, self._sys_timing, self._sys_error)
        self._mic_stream = self._open_stream(
            self.mic_info, self._mic_frames, self._mic_timing, self._mic_error)

    def _open_stream(self, device_info, frame_list, timing, error_holder):
        channels = min(int(device_info["maxInputChannels"]), 2) or 1
        rate = int(device_info["defaultSampleRate"])
        timing["channels"] = channels
        timing["nominal_rate"] = rate

        def callback(in_data, frame_count, time_info, status):
            frame_list.append(in_data)
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
# Detector de reuniones de Teams (heurístico, en segundo plano)
# --------------------------------------------------------------------------

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

    def _teams_pids(self):
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

    def _meeting_window_open(self, teams_pids):
        """True si alguna ventana visible PERTENECIENTE A TEAMS tiene un título
        con palabra clave de reunión. Se restringe a ventanas de Teams para no
        dar falsos positivos con otras ventanas (p.ej. la propia app, que se
        llama 'Grabador de Reuniones Teams', un Word titulado 'meeting', etc.)."""
        if not WIN32_AVAILABLE or not teams_pids:
            return False
        found = {"value": False}

        def enum_handler(hwnd, _):
            if found["value"] or not win32gui.IsWindowVisible(hwnd):
                return
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:
                return
            if pid not in teams_pids:
                return
            title = win32gui.GetWindowText(hwnd)
            if not title:
                return
            title_l = title.lower()
            if any(k in title_l for k in self.keywords):
                found["value"] = True

        try:
            win32gui.EnumWindows(enum_handler, None)
        except Exception:
            return False
        return found["value"]

    def run(self):
        while not self._stop_event.is_set():
            try:
                mic = self._teams_using_mic()
                if mic is None:
                    # Sin registro: respaldo por título de ventana de Teams.
                    teams_pids = self._teams_pids()
                    meeting_now = bool(teams_pids) and self._meeting_window_open(teams_pids)
                else:
                    # Señal fiable por micrófono. Confirmamos que Teams sigue vivo
                    # (evita una entrada obsoleta si Teams murió reteniendo el micro).
                    meeting_now = bool(mic) and bool(self._teams_pids())
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
# Popup de aviso estilo notificación
# --------------------------------------------------------------------------

class MeetingPopup(tk.Toplevel):
    def __init__(self, master, on_accept, on_dismiss=None, timeout=30):
        super().__init__(master)
        self.on_accept = on_accept
        self.on_dismiss = on_dismiss
        self._closed = False

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        w, h = 340, 140
        sx, sy = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{w}x{h}+{sx - w - 24}+{sy - h - 70}")
        self.configure(bg="#202124", highlightthickness=1, highlightbackground="#444")

        tk.Label(self, text="🎥 Reunión de Teams detectada",
                 bg="#202124", fg="white", font=("Segoe UI", 11, "bold"),
                 anchor="w", justify="left").pack(fill="x", padx=14, pady=(14, 2))
        tk.Label(self, text="¿Quieres iniciar la grabación ahora?",
                 bg="#202124", fg="#cccccc", anchor="w", justify="left").pack(
            fill="x", padx=14, pady=(0, 10))

        btns = tk.Frame(self, bg="#202124")
        btns.pack(padx=14, pady=4, fill="x")
        tk.Button(btns, text="● Grabar", bg="#2ecc71", fg="white", relief="flat",
                  command=self._accept, width=10).pack(side="left", padx=(0, 8))
        tk.Button(btns, text="Ignorar", bg="#3a3a3a", fg="white", relief="flat",
                  command=self._dismiss, width=10).pack(side="left")

        self.after(timeout * 1000, self._auto_dismiss)

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
# GUI principal
# --------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self, start_in_tray=False):
        super().__init__()
        self.title("Grabador de Reuniones Teams — Transcripción")
        self.geometry("860x620")

        self.recorder = None
        self.recording = False
        self.last_mixed_path = None
        self.transcript_text = ""
        self.meeting_watcher = None
        self.tray_icon = None

        self.output_dir = tk.StringVar(value=OUTPUT_DIR_DEFAULT)
        self.auto_detect_var = tk.BooleanVar(value=False)
        self.keywords_var = tk.StringVar(value=", ".join(DEFAULT_MEETING_KEYWORDS))
        self.poll_interval_var = tk.IntVar(value=4)
        self.autostart_var = tk.BooleanVar(value=is_autostart_enabled())

        self._build_ui()
        self._refresh_mic_list()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if TRAY_AVAILABLE:
            self._setup_tray()

        # Arranque en modo bandeja (inicio automático al iniciar sesión):
        # ocultar la ventana y activar la detección de reuniones.
        if start_in_tray:
            self.after(0, self._start_in_tray)

    def _start_in_tray(self):
        if PSUTIL_AVAILABLE and WIN32_AVAILABLE:
            self.auto_detect_var.set(True)
            self._toggle_auto_detect()
        if TRAY_AVAILABLE:
            self.withdraw()

    # ---------------- Construcción de la UI ----------------

    def _build_ui(self):
        pad = {"padx": 8, "pady": 6}

        frame_out = ttk.LabelFrame(self, text="Carpeta de salida")
        frame_out.pack(fill="x", **pad)
        ttk.Entry(frame_out, textvariable=self.output_dir, width=70).pack(
            side="left", padx=6, pady=6, fill="x", expand=True)
        ttk.Button(frame_out, text="Elegir...", command=self._choose_output_dir).pack(
            side="left", padx=6, pady=6)

        frame_dev = ttk.LabelFrame(self, text="Dispositivo de entrada (micrófono)")
        frame_dev.pack(fill="x", **pad)
        self.mic_combo = ttk.Combobox(frame_dev, state="readonly", width=60)
        self.mic_combo.pack(side="left", padx=6, pady=6, fill="x", expand=True)
        ttk.Button(frame_dev, text="Refrescar", command=self._refresh_mic_list).pack(
            side="left", padx=6, pady=6)
        ttk.Label(frame_dev, text="(el audio del sistema se detecta solo vía WASAPI loopback)").pack(
            side="left", padx=6)

        frame_rec = ttk.LabelFrame(self, text="Grabación")
        frame_rec.pack(fill="x", **pad)
        self.btn_start = ttk.Button(frame_rec, text="● Iniciar grabación", command=self._start_recording)
        self.btn_start.pack(side="left", padx=6, pady=8)
        self.btn_stop = ttk.Button(frame_rec, text="■ Detener", command=self._stop_recording, state="disabled")
        self.btn_stop.pack(side="left", padx=6, pady=8)
        self.lbl_timer = ttk.Label(frame_rec, text="00:00:00", font=("Consolas", 14))
        self.lbl_timer.pack(side="left", padx=20)
        self.lbl_status = ttk.Label(frame_rec, text="Listo.")
        self.lbl_status.pack(side="left", padx=10)

        # --- Modo en segundo plano / detección automática ---
        frame_bg = ttk.LabelFrame(self, text="Modo en segundo plano (detección de reuniones de Teams)")
        frame_bg.pack(fill="x", **pad)
        row1 = ttk.Frame(frame_bg)
        row1.pack(fill="x", padx=4, pady=2)
        ttk.Checkbutton(row1, text="Detectar reuniones automáticamente y avisar",
                         variable=self.auto_detect_var, command=self._toggle_auto_detect).pack(side="left")
        ttk.Label(row1, text="Sondeo (seg):").pack(side="left", padx=(20, 4))
        ttk.Spinbox(row1, from_=2, to=30, width=4, textvariable=self.poll_interval_var).pack(side="left")
        ttk.Button(row1, text="Minimizar a bandeja", command=self._minimize_to_tray).pack(side="right", padx=4)
        ttk.Button(row1, text="Probar aviso", command=self._test_popup).pack(side="right", padx=4)

        row2 = ttk.Frame(frame_bg)
        row2.pack(fill="x", padx=4, pady=(2, 6))
        ttk.Label(row2, text="Palabras clave (solo respaldo por título si no hay registro):").pack(side="left")
        ttk.Entry(row2, textvariable=self.keywords_var, width=45).pack(
            side="left", padx=6, fill="x", expand=True)

        row3 = ttk.Frame(frame_bg)
        row3.pack(fill="x", padx=4, pady=(2, 6))
        ttk.Checkbutton(
            row3, text="Arrancar automáticamente al iniciar sesión (minimizado a la bandeja)",
            variable=self.autostart_var, command=self._toggle_autostart).pack(side="left")
        if not WINREG_AVAILABLE:
            ttk.Label(row3, text="(no disponible en esta plataforma)", foreground="#b33").pack(side="left", padx=6)

        note = ("Detección principal: se considera 'en reunión' cuando Teams está usando el micrófono "
                "(lo toma al entrar en una llamada y lo suelta al salir). Es fiable y no depende del "
                "título de la ventana. Las palabras clave de arriba solo se usan como respaldo si no "
                "hubiera acceso al registro de Windows.")
        ttk.Label(frame_bg, text=note, wraplength=800, foreground="#666").pack(
            fill="x", padx=6, pady=(0, 6))

        if not (PSUTIL_AVAILABLE and WIN32_AVAILABLE):
            ttk.Label(frame_bg,
                      text="⚠ Faltan dependencias para la detección automática: pip install psutil pywin32",
                      foreground="#b33").pack(fill="x", padx=6)
        if not TRAY_AVAILABLE:
            ttk.Label(frame_bg,
                      text="⚠ Para minimizar a la bandeja del sistema instala: pip install pystray pillow",
                      foreground="#b33").pack(fill="x", padx=6)

        # --- Transcripción ---
        frame_tr = ttk.LabelFrame(self, text="Transcripción (faster-whisper, local)")
        frame_tr.pack(fill="both", expand=True, **pad)

        tr_controls = ttk.Frame(frame_tr)
        tr_controls.pack(fill="x")
        ttk.Label(tr_controls, text="Modelo:").pack(side="left", padx=4)
        self.whisper_model_combo = ttk.Combobox(
            tr_controls, state="readonly", width=12,
            values=["tiny", "base", "small", "medium", "large-v3"])
        self.whisper_model_combo.set("small")
        self.whisper_model_combo.pack(side="left", padx=4)
        ttk.Label(tr_controls, text="Idioma (vacío = auto):").pack(side="left", padx=4)
        self.lang_entry = ttk.Entry(tr_controls, width=6)
        self.lang_entry.insert(0, "es")
        self.lang_entry.pack(side="left", padx=4)
        self.btn_transcribe = ttk.Button(
            tr_controls, text="Transcribir última grabación",
            command=self._transcribe, state="disabled")
        self.btn_transcribe.pack(side="left", padx=10)
        ttk.Button(tr_controls, text="Guardar .txt", command=self._save_transcript).pack(side="left", padx=4)

        self.txt_transcript = scrolledtext.ScrolledText(frame_tr, height=14, wrap="word")
        self.txt_transcript.pack(fill="both", expand=True, padx=4, pady=4)

        note_resumen = ("El resumen de las transcripciones se genera aparte mediante tu tarea "
                        "programada de Claude sobre los .txt guardados; esta app ya no lo hace.")
        ttk.Label(frame_tr, text=note_resumen, wraplength=800, foreground="#666").pack(
            fill="x", padx=6, pady=(0, 4))

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
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.btn_transcribe.config(state="disabled")
        self.lbl_status.config(text="Grabando audio del sistema + micrófono...")
        self.deiconify()
        self._tick_timer()

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
        self.btn_stop.config(state="disabled")
        self.lbl_status.config(text="Procesando y alineando pistas...")
        output_dir = self.output_dir.get()
        recorder = self.recorder
        self.recorder = None
        threading.Thread(
            target=self._stop_worker, args=(recorder, output_dir), daemon=True).start()

    def _stop_worker(self, recorder, output_dir):
        try:
            mixed, rate, sys_only, mic_only = recorder.stop_and_mix()
            errors = recorder.get_errors()
            recorder.close()

            os.makedirs(output_dir, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            mixed_path = os.path.join(output_dir, f"reunion_{stamp}.wav")
            sys_path = os.path.join(output_dir, f"reunion_{stamp}_sistema.wav")
            mic_path = os.path.join(output_dir, f"reunion_{stamp}_mic.wav")

            save_wav_mono(mixed_path, mixed, rate)
            save_wav_mono(sys_path, sys_only, rate)
            save_wav_mono(mic_path, mic_only, rate)

            self.after(0, lambda: self._on_stop_done(mixed_path, errors))
        except Exception as e:
            try:
                recorder.close()
            except Exception:
                pass
            self.after(0, lambda: self._on_stop_error(e))

    def _on_stop_done(self, mixed_path, errors):
        self.btn_start.config(state="normal")
        self.btn_transcribe.config(state="normal")
        self.last_mixed_path = mixed_path
        self.lbl_status.config(text=f"Grabación guardada: {mixed_path}")
        if errors:
            messagebox.showwarning("Avisos durante la grabación", "\n".join(errors))

    def _on_stop_error(self, error):
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.lbl_status.config(text="Error al procesar la grabación.")
        messagebox.showerror("Error al detener la grabación", str(error))

    # ---------------- Detección de reuniones ----------------

    def _toggle_auto_detect(self):
        if self.auto_detect_var.get():
            if not (PSUTIL_AVAILABLE and WIN32_AVAILABLE):
                messagebox.showerror(
                    "Faltan dependencias",
                    "Instala: pip install psutil pywin32")
                self.auto_detect_var.set(False)
                return
            keywords = [k for k in self.keywords_var.get().split(",")]
            self.meeting_watcher = MeetingWatcher(
                on_meeting_start=self._on_meeting_detected,
                on_meeting_end=None,
                keywords=keywords,
                poll_interval=self.poll_interval_var.get(),
            )
            self.meeting_watcher.start()
            self.lbl_status.config(text="Detección automática activada. Vigilando reuniones de Teams...")
        else:
            if self.meeting_watcher:
                self.meeting_watcher.stop()
                self.meeting_watcher = None
            self.lbl_status.config(text="Detección automática desactivada.")

    def _on_meeting_detected(self):
        # Llamado desde el hilo del watcher: hay que saltar al hilo de Tkinter.
        self.after(0, self._show_meeting_popup)

    def _show_meeting_popup(self):
        if self.recording:
            return  # ya está grabando, no molestar
        MeetingPopup(self, on_accept=self._start_recording)

    def _test_popup(self):
        self._show_meeting_popup()

    # ---------------- Inicio automático ----------------

    def _toggle_autostart(self):
        if not WINREG_AVAILABLE:
            messagebox.showerror("No disponible", "El inicio automático solo está disponible en Windows.")
            self.autostart_var.set(False)
            return
        try:
            if self.autostart_var.get():
                enable_autostart()
                self.lbl_status.config(text="Inicio automático activado: arrancará minimizado en la bandeja al iniciar sesión.")
            else:
                disable_autostart()
                self.lbl_status.config(text="Inicio automático desactivado.")
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
        self.tray_icon = pystray.Icon("grabador_teams", image, "Grabador de Reuniones Teams", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _make_tray_image(self):
        img = Image.new("RGB", (64, 64), "#202124")
        d = ImageDraw.Draw(img)
        d.ellipse((14, 14, 50, 50), fill="#e74c3c")
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
        if not WHISPER_AVAILABLE:
            messagebox.showerror(
                "Falta dependencia",
                "Instala faster-whisper:\npip install faster-whisper")
            return
        if not self.last_mixed_path or not os.path.exists(self.last_mixed_path):
            messagebox.showwarning("Aviso", "Primero graba y detén una grabación.")
            return

        self.btn_transcribe.config(state="disabled")
        self.lbl_status.config(text="Transcribiendo (puede tardar según el modelo)...")
        threading.Thread(target=self._transcribe_worker, daemon=True).start()

    def _transcribe_worker(self):
        try:
            model_size = self.whisper_model_combo.get()
            lang = self.lang_entry.get().strip() or None
            model = WhisperModel(model_size, device="cpu", compute_type="int8")
            segments, info = model.transcribe(self.last_mixed_path, language=lang)
            full_text = " ".join(seg.text.strip() for seg in segments)
            self.transcript_text = full_text
            self.after(0, lambda: self._on_transcribe_done(full_text))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error en transcripción", str(e)))
            self.after(0, lambda: self.lbl_status.config(text="Error al transcribir."))
        finally:
            self.after(0, lambda: self.btn_transcribe.config(state="normal"))

    def _on_transcribe_done(self, text):
        self.txt_transcript.delete("1.0", tk.END)
        self.txt_transcript.insert(tk.END, text)
        self.lbl_status.config(text="Transcripción completada.")

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

    parser = argparse.ArgumentParser(description="Grabador de Reuniones de Teams")
    parser.add_argument(
        "--tray", action="store_true",
        help="Arrancar minimizado en la bandeja con la detección de reuniones activada "
             "(lo usa el inicio automático al iniciar sesión).")
    args = parser.parse_args()

    app = App(start_in_tray=args.tray)
    app.mainloop()