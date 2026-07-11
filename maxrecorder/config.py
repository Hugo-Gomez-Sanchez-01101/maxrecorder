"""Constantes de configuración y persistencia de ajustes (config.json)."""

import os
import json

# Raíz del proyecto (carpeta que contiene grabador.py y este paquete).
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY_SCRIPT = os.path.join(ROOT_DIR, "grabador.py")

_DOCS_MAX_RECORDER = os.path.join(os.path.expanduser("~"), "Documents", "MaxRecorder")
OUTPUT_DIR_DEFAULT = os.path.join(_DOCS_MAX_RECORDER, "Records")
TRANSCRIPT_DIR_DEFAULT = os.path.join(_DOCS_MAX_RECORDER, "Transcripts")

DEFAULT_MEETING_KEYWORDS = ["reunión", "reunion", "meeting", "llamada", "call",
                            "[Weekly] Hacking Team"]

# Nombre del .txt de transcripción: por defecto reunion_AAAA-MM-DD.txt. Si el
# título de alguna ventana de Teams al empezar a grabar contiene una de estas
# subcadenas, se usa su prefijo en su lugar (p.ej. weekly_AAAA-MM-DD.txt).
DEFAULT_TRANSCRIPT_PREFIX = "reunion"
MEETING_NAME_RULES = [
    ("[weekly] hacking team", "weekly"),
]

# Ajustes persistentes (carpetas, palabras clave, sondeo) en la raíz del proyecto.
CONFIG_PATH = os.path.join(ROOT_DIR, "config.json")


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
