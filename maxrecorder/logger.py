"""Logging setup: rotating file at logs/maxrecorder.log (plus console when one
is attached). Call setup_logging() once at startup; modules then use the
standard `logging.getLogger(__name__)`."""

import os
import sys
import logging
from logging.handlers import RotatingFileHandler

from .config import ROOT_DIR

LOG_DIR = os.path.join(ROOT_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "maxrecorder.log")


def setup_logging(level=logging.INFO):
    """Configures the root logger. Idempotent: calling it twice does nothing."""
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(level)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")

    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError:
        pass

    # Console output only when there is one (pythonw.exe has no stderr).
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)

    # Keep third-party libraries from flooding the log.
    for noisy in ("urllib3", "requests", "faster_whisper", "PIL", "pystray"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
