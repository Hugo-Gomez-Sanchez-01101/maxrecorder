"""Motor de transcripción (faster-whisper): caché de modelo, filtro VAD,
streaming por segmentos y modo multi-pista con etiquetas de hablante."""

import os
import threading
from datetime import datetime

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
    """Ruta del .txt de transcripción: <prefijo>_AAAA-MM-DD.txt en 'directory'.
    Si ya existe uno de ese día (otra reunión), añade _2, _3... para no pisarlo."""
    date = datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(directory, f"{prefix}_{date}.txt")
    n = 2
    while os.path.exists(path):
        path = os.path.join(directory, f"{prefix}_{date}_{n}.txt")
        n += 1
    return path


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
