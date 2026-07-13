"""Transcription engine (faster-whisper): model cache, VAD filter, segment
streaming and multi-track mode with speaker labels."""

import os
import logging
import threading
from datetime import datetime

from .i18n import tr

log = logging.getLogger(__name__)

try:
    from faster_whisper import WhisperModel
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False


def format_ts(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def transcript_txt_path(directory: str, prefix: str) -> str:
    """Transcript .txt path: <prefix>_YYYY-MM-DD.txt in 'directory'. If one for
    that day already exists (another meeting), appends _2, _3... to avoid
    overwriting it."""
    date = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(directory, f"{prefix}_{date}.txt")
    n = 2
    while os.path.exists(path):
        path = os.path.join(directory, f"{prefix}_{date}_{n}.txt")
        n += 1
    return path


class Transcriber:
    """Wraps faster-whisper with:
      - model cache (loading is the slowest step; it is reused between uses),
      - VAD filter to skip silences,
      - progress and segment callbacks (for streaming in the UI),
      - multi-track mode with speaker labels (You / Them)."""

    def __init__(self):
        self._models = {}
        self._lock = threading.Lock()

    def get_model(self, model_size: str):
        with self._lock:
            if model_size not in self._models:
                log.info("Loading Whisper model '%s' (first use, may download)", model_size)
                self._models[model_size] = WhisperModel(
                    model_size, device="cpu", compute_type="int8")
                log.info("Whisper model '%s' ready", model_size)
            return self._models[model_size]

    def transcribe_jobs(self, jobs, model_size, language=None,
                        on_segment=None, on_progress=None, on_phase=None):
        """jobs: list of (label|None, path). Returns a list of segments
        (start, label, text) ordered by time. Callbacks arrive from this same
        thread (the caller decides how to hop to the UI thread)."""
        if on_phase:
            on_phase(tr("Loading model '{}'...").format(model_size))
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
                on_phase(tr("Transcribing {}").format(name)
                         + (f" [{label}]" if label else "") + "...")
            segments, info = model.transcribe(path, **kwargs)
            duration = max(getattr(info, "duration", 0.0) or 0.0, 0.001)
            log.info("Transcribing %s (label=%s, %.1f min)",
                     os.path.basename(path), label, duration / 60)
            for seg in segments:
                text = seg.text.strip()
                # Discard segments the model itself considers non-speech
                # (a common source of hallucinations in silences).
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
