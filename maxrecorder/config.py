"""Configuration constants and settings persistence (config.json)."""

import os
import json

# Project root (folder containing grabador.py and this package).
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY_SCRIPT = os.path.join(ROOT_DIR, "grabador.py")

_DOCS_MAX_RECORDER = os.path.join(os.path.expanduser("~"), "Documents", "MaxRecorder")
RECORD_DIR_DEFAULT = os.path.join(_DOCS_MAX_RECORDER, "Records")
TRANSCRIPT_DIR_DEFAULT = os.path.join(_DOCS_MAX_RECORDER, "Transcripts")

DEFAULT_MEETING_KEYWORDS = ["meeting", "call", "weekly", "monthly", "daily"]

# Transcript .txt name: meeting_YYYY-MM-DD.txt by default. If a Teams window
# title at the moment recording starts contains one of these substrings, its
# prefix is used instead (e.g. weekly_YYYY-MM-DD.txt).
DEFAULT_TRANSCRIPT_PREFIX = "meeting"
MEETING_NAME_RULES = [
    ("[weekly] hacking team", "weekly"),
]

# Persistent settings (folders, keywords, poll interval) in the project root.
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


def load_dotenv_vars() -> dict:
    """Minimal .env reader (KEY=value lines) from the project root. Used only
    as default values for the AI-summary credentials, so an existing .env
    keeps working without extra dependencies."""
    env = {}
    try:
        with open(os.path.join(ROOT_DIR, ".env"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return env
