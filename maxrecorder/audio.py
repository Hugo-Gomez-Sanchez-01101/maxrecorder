"""Audio capture and processing: mixing/alignment helpers and the dual
recorder (system loopback + microphone) over WASAPI."""

import time
import wave
import threading  # noqa: F401  (documents that DualRecorder is used across threads)

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
# Helpers: resampling, time alignment and mixing
# --------------------------------------------------------------------------

def resample_linear(data: np.ndarray, orig_rate: float, target_rate: int) -> np.ndarray:
    """Resample a 1D (mono) array to target_rate. orig_rate can be the real
    "effective" rate (num samples / real wall-clock duration), which corrects
    the drift between the device clock and real time."""
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
    """Corrects a residual offset (milliseconds) between tracks using cross-
    correlation over an initial window, taking advantage of the faint speaker
    leak the mic usually picks up. Returns the shift in samples to apply to
    'other' (positive = delay 'other'). If the correlation is not reliable
    (e.g. you use headphones, no audio leak), returns 0 and changes nothing."""
    if not SCIPY_AVAILABLE:
        return 0
    n = int(window_sec * rate)
    a = reference[:n].astype(np.float64)
    b = other[:n].astype(np.float64)
    if len(a) < rate or len(b) < rate:
        return 0
    a = a - a.mean()
    b = b - b.mean()
    # downsample to ~2000 Hz so the correlation is fast
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
    """Shifts 'arr' shift_samples forward (positive) or trims it from the
    front (negative)."""
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
# Dual recorder (system + microphone) with wall-clock timestamps
# --------------------------------------------------------------------------

class DualRecorder:
    """Records the system loopback and the chosen microphone in parallel,
    storing real start/end timestamps for each track so they can be aligned
    correctly when mixing. Also exposes the instantaneous RMS level of each
    track (for the UI visualizer)."""

    def __init__(self, mic_device_index=None):
        if pyaudio is None:
            raise RuntimeError(
                "PyAudioWPatch is not installed or you are not on Windows. "
                "Install with: pip install PyAudioWPatch"
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
                "Could not find the loopback device matching the default audio "
                "output. Enable 'Stereo Mix' in Windows or install VB-Cable as "
                "an alternative."
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

        # We capture with callbacks (not blocking reads): WASAPI loopback
        # blocks stream.read() when nothing is playing, which left threads
        # hanging and caused a native crash on close. With a callback,
        # PortAudio hands us the data and shutdown is clean and safe.
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
            # Normalized RMS level (0..1) with smoothing, for the visualizer.
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
            error_holder["error"] = f"Could not open '{device_info['name']}': {e}"
            return None
        timing["start"] = time.perf_counter()
        return stream

    def elapsed_seconds(self):
        if not self._recording or self._wall_start is None:
            return 0
        return time.time() - self._wall_start

    def get_levels(self):
        """(system_level, microphone_level) in 0..1, smoothed."""
        return self._sys_level["v"], self._mic_level["v"]

    def get_errors(self):
        errs = []
        if "error" in self._sys_error:
            errs.append("System audio: " + self._sys_error["error"])
        if "error" in self._mic_error:
            errs.append("Microphone: " + self._mic_error["error"])
        return errs

    def stop_and_mix(self, target_rate: int = TARGET_RATE, fine_sync: bool = True):
        """Stops recording, time-aligns both tracks (start offset + real-clock
        drift correction + optional fine adjustment by cross-correlation) and
        returns (mixed, rate, sys_track, mic_track)."""
        # End marker (wall clock) for both streams before stopping them.
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

        # --- Resample by the device NOMINAL rate ---
        # NOTE: do NOT use "samples/wall-clock duration" as the effective rate.
        # WASAPI loopback does not deliver samples during silences (and the
        # first callback buffer arrives with latency), so the system sample
        # count is lower than the wall-clock duration -> the effective rate
        # would come out too low and the audio would be STRETCHED (sounds
        # slowed down and low-pitched). The real clock drift is <0.5% and
        # imperceptible, so the nominal rate is the correct, robust choice.
        sys_nom_rate = self._sys_timing.get("nominal_rate", target_rate)
        mic_nom_rate = self._mic_timing.get("nominal_rate", target_rate)

        sys_target = resample_linear(sys_mono, sys_nom_rate, target_rate)
        mic_target = resample_linear(mic_mono, mic_nom_rate, target_rate)

        # --- Start-offset alignment between the two threads ---
        sys_start = self._sys_timing.get("start")
        mic_start = self._mic_timing.get("start")
        if sys_start is not None and mic_start is not None:
            t0_ref = min(sys_start, mic_start)
            sys_target = pad_front(sys_target, int(round((sys_start - t0_ref) * target_rate)))
            mic_target = pad_front(mic_target, int(round((mic_start - t0_ref) * target_rate)))

        # --- Optional fine adjustment by cross-correlation (speaker leak
        # picked up by the microphone). Ignored if not reliable. ---
        if fine_sync:
            shift = estimate_fine_shift(sys_target, mic_target, target_rate)
            if shift != 0:
                mic_target = apply_shift(mic_target, shift)

        mixed = mix_tracks_equal_len(sys_target, mic_target, gain_a=0.85, gain_b=1.0)
        sys_out = np.clip(sys_target, -32768, 32767).astype(np.int16)
        mic_out = np.clip(mic_target, -32768, 32767).astype(np.int16)

        return mixed, target_rate, sys_out, mic_out

    def close(self):
        # Idempotent: avoid a second terminate() on an already-closed PortAudio.
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
