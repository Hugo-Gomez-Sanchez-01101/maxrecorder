"""Arranque automático al iniciar sesión (clave Run del registro de Windows,
por usuario, sin permisos de administrador)."""

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
# Nombre antiguo (versiones previas); se limpia al desactivar el inicio automático.
AUTOSTART_LEGACY_NAMES = ("GrabadorTeams",)


def _autostart_command() -> str:
    """Comando que Windows ejecutará al iniciar sesión. Arranca la app en modo
    bandeja (--tray). Si está congelada con PyInstaller usa el propio .exe;
    si no, usa pythonw.exe (sin consola) sobre el script de entrada."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --tray'
    exe = sys.executable
    # pythonw.exe evita la ventana de consola al arrancar en segundo plano
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pythonw):
        exe = pythonw
    return f'"{exe}" "{ENTRY_SCRIPT}" --tray'


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
