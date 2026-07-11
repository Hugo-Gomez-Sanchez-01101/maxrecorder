"""Captura y procesado de audio: utilidades de mezcla/alineación y el
grabador dual (loopback del sistema + micrófono) sobre WASAPI."""

import time
import wave
import threading  # noqa: F401  (documenta que DualRecorder se usa entre hilos)

import numpy as np

try:
    import pyaudiowpatch as pyaudio
except ImportError:
    pyaudio = None

try:
    from scipy.signal import fftconvolve
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False


CHUNK = 1024
FORMAT_WIDTH = 2  # int16
TARGET_RATE = 44100


# --------------------------------------------------------------------------
# Utilidades: resample, alineación temporal y mezcla
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
