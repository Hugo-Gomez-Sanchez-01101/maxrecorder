"""Automatic startup at login (Run key in the Windows registry, per user,
without administrator permissions)."""

import os
import sys

from .config import ENTRY_SCRIPT

try:
    import winreg
    WINREG_AVAILABLE = True
except ImportError:
    WINREG_AVAILABLE = False


AUTOSTART_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_VALUE_NAME = "MaxRecorder"
# Old names (previous versions); cleaned up when autostart is disabled.
AUTOSTART_LEGACY_NAMES = ("GrabadorTeams",)


def _autostart_command() -> str:
    """Command Windows will run at login. Starts the app in tray mode (--tray).
    If frozen with PyInstaller it uses the .exe itself; otherwise it uses
    pythonw.exe (no console) on the entry script."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --tray'
    exe = sys.executable
    # pythonw.exe avoids the console window when starting in the background
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pythonw):
        exe = pythonw
    return f'"{exe}" "{ENTRY_SCRIPT}" --tray'


def _registered_command():
    """Command currently stored in the registry (or None if there is no entry)."""
    if not WINREG_AVAILABLE:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, AUTOSTART_VALUE_NAME)
        return value
    except OSError:
        return None


def is_autostart_enabled() -> bool:
    return _registered_command() is not None


def refresh_autostart_if_enabled():
    """If autostart is enabled but the registry entry points to a path other
    than the current one (e.g. because the project folder was moved or
    renamed), rewrites it with the correct command. Returns True if it updated
    it. Safe to call on every startup."""
    if not WINREG_AVAILABLE:
        return False
    current = _registered_command()
    if current is None:
        return False
    wanted = _autostart_command()
    if current == wanted:
        return False
    try:
        enable_autostart()
        return True
    except OSError:
        return False


def enable_autostart():
    if not WINREG_AVAILABLE:
        raise RuntimeError("The Windows registry is not available on this platform.")
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
