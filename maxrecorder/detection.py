"""Detección de reuniones de Teams: procesos, títulos de ventana, uso del
micrófono según el registro de Windows y el watcher en segundo plano."""

import threading

from .config import DEFAULT_MEETING_KEYWORDS, DEFAULT_TRANSCRIPT_PREFIX, MEETING_NAME_RULES

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


TEAMS_PROCESS_NAMES = {"teams.exe", "ms-teams.exe"}

# Detección de "en reunión" por uso del micrófono (fiable, sin depender del
# título de ventana). Windows registra aquí, por app, cuándo empezó/terminó de
# usar el micro; si LastUsedTimeStop == 0, la app lo está usando AHORA mismo.
MIC_CONSENT_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
    r"\ConsentStore\microphone"
)


def teams_pids():
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


def teams_window_titles(pids):
    """Títulos de las ventanas visibles pertenecientes a los procesos dados."""
    titles = []
    if not WIN32_AVAILABLE or not pids:
        return titles

    def enum_handler(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return
        if pid not in pids:
            return
        title = win32gui.GetWindowText(hwnd)
        if title:
            titles.append(title)

    try:
        win32gui.EnumWindows(enum_handler, None)
    except Exception:
        pass
    return titles


def detect_meeting_prefix() -> str:
    """Prefijo para el nombre del .txt según el título de la reunión de Teams
    en curso (ver MEETING_NAME_RULES). Si no se puede determinar o no hay
    regla que aplique, devuelve el prefijo por defecto ('reunion')."""
    if not (WIN32_AVAILABLE and PSUTIL_AVAILABLE):
        return DEFAULT_TRANSCRIPT_PREFIX
    try:
        titles = teams_window_titles(teams_pids())
    except Exception:
        return DEFAULT_TRANSCRIPT_PREFIX
    for title in titles:
        title_l = title.lower()
        for substring, prefix in MEETING_NAME_RULES:
            if substring in title_l:
                return prefix
    return DEFAULT_TRANSCRIPT_PREFIX


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

    def _meeting_window_open(self, pids):
        """True si alguna ventana visible PERTENECIENTE A TEAMS tiene un título
        con palabra clave de reunión. Se restringe a ventanas de Teams para no
        dar falsos positivos con otras ventanas (p.ej. la propia app 'Max
        Recorder', un Word titulado 'meeting', etc.)."""
        for title in teams_window_titles(pids):
            title_l = title.lower()
            if any(k in title_l for k in self.keywords):
                return True
        return False

    def run(self):
        while not self._stop_event.is_set():
            try:
                mic = self._teams_using_mic()
                if mic is None:
                    # Sin registro: respaldo por título de ventana de Teams.
                    pids = teams_pids()
                    meeting_now = bool(pids) and self._meeting_window_open(pids)
                else:
                    # Señal fiable por micrófono. Confirmamos que Teams sigue vivo
                    # (evita una entrada obsoleta si Teams murió reteniendo el micro).
                    meeting_now = bool(mic) and bool(teams_pids())
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
