"""Teams meeting detection: processes, window titles, microphone usage from
the Windows registry, and the background watcher."""

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

# "In a meeting" detection by microphone usage (reliable, without depending on
# the window title). Windows records here, per app, when it started/stopped
# using the mic; if LastUsedTimeStop == 0, the app is using it RIGHT NOW.
MIC_CONSENT_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
    r"\ConsentStore\microphone"
)


def teams_pids():
    """PIDs of running Teams processes (empty if none)."""
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
    """Titles of the visible windows belonging to the given processes."""
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
    """Prefix for the .txt name based on the title of the current Teams meeting
    (see MEETING_NAME_RULES). If it cannot be determined or no rule applies,
    returns the default prefix ('meeting')."""
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
    """Periodically polls whether Teams is in a meeting/call.

    PRIMARY signal (reliable): that Teams is using the microphone at that
    moment, according to the Windows registry (CapabilityAccessManager). Teams
    grabs the mic when it joins a call and releases it when it leaves, so this
    is a much more robust signal than the window title (which only carries the
    meeting name).

    FALLBACK signal (if there is no registry access): a running Teams process
    + a visible window whose title contains a keyword. Less reliable; only used
    as a last resort."""

    def __init__(self, on_meeting_start, on_meeting_end=None,
                 keywords=None, poll_interval=4):
        super().__init__(daemon=True)
        self.on_meeting_start = on_meeting_start
        self.on_meeting_end = on_meeting_end
        # keywords: only used by the fallback window-title method.
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
        """True if any subkey whose name contains 'teams' has
        LastUsedTimeStop == 0 (microphone in use right now)."""
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
        """True/False whether Teams is using the microphone now. Returns None if
        it cannot be determined from the registry (to fall back to the other
        method)."""
        if not WINREG_AVAILABLE:
            return None
        try:
            return (self._consent_teams_active(MIC_CONSENT_KEY)
                    or self._consent_teams_active(MIC_CONSENT_KEY + r"\NonPackaged"))
        except Exception:
            return None

    def _meeting_window_open(self, pids):
        """True if any visible window BELONGING TO TEAMS has a title with a
        meeting keyword. Restricted to Teams windows to avoid false positives
        with other windows (e.g. the 'Max Recorder' app itself, a Word document
        titled 'meeting', etc.)."""
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
                    # No registry: fall back to the Teams window title.
                    pids = teams_pids()
                    meeting_now = bool(pids) and self._meeting_window_open(pids)
                else:
                    # Reliable mic signal. We confirm Teams is still alive
                    # (avoids a stale entry if Teams died holding the mic).
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
