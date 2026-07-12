"""
Teams meeting detection diagnostics.

1) Lists the visible Teams windows (title method, not very reliable).
2) Dumps the CURRENT microphone usage per app from the Windows registry
   (CapabilityAccessManager). When an app is using the mic RIGHT NOW, its
   'LastUsedTimeStop' value is 0. This is the robust signal to tell whether
   Teams is in a call/meeting, without depending on the window title.

Usage:  python diag_teams.py
Run it once OUTSIDE a meeting and once INSIDE, and compare the [MIC] section.
"""

import winreg
import psutil
import win32gui
import win32process

TEAMS_PROCESS_NAMES = {"teams.exe", "ms-teams.exe"}

MIC_CONSENT_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
    r"\ConsentStore\microphone"
)


def teams_pids():
    pids = {}
    for proc in psutil.process_iter(["name", "pid"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name in TEAMS_PROCESS_NAMES or "teams" in name:
                pids[proc.info["pid"]] = name
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return pids


def _read_value(key, name):
    try:
        return winreg.QueryValueEx(key, name)[0]
    except FileNotFoundError:
        return None
    except OSError:
        return None


def _iter_consent_apps(root, subpath):
    """Yields (app_name, LastUsedTimeStart, LastUsedTimeStop) for each subkey."""
    try:
        key = winreg.OpenKey(root, subpath)
    except OSError:
        return
    with key:
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(key, i)
            except OSError:
                break
            i += 1
            with winreg.OpenKey(key, sub) as sk:
                start = _read_value(sk, "LastUsedTimeStart")
                stop = _read_value(sk, "LastUsedTimeStop")
                yield sub, start, stop


def dump_mic_usage():
    print("\n[MIC] Microphone usage per app (Stop==0 => using it NOW):")
    rows = []
    # Packaged apps (the new Teams is MSIX: 'MSTeams_8wekyb3d8bbwe')
    for name, start, stop in _iter_consent_apps(winreg.HKEY_CURRENT_USER, MIC_CONSENT_KEY):
        if name.lower() == "nonpackaged":
            continue
        rows.append((name, start, stop))
    # Non-packaged apps (classic Teams)
    for name, start, stop in _iter_consent_apps(
            winreg.HKEY_CURRENT_USER, MIC_CONSENT_KEY + r"\NonPackaged"):
        rows.append((name, start, stop))

    if not rows:
        print("  (no entries; maybe no app has used the mic)")
    for name, start, stop in rows:
        in_use = "  <<< IN USE NOW" if stop == 0 else ""
        is_teams = "teams" in name.lower()
        mark = " [TEAMS]" if is_teams else ""
        print(f"  {name}{mark}: start={start} stop={stop}{in_use}")


def dump_windows():
    pids = teams_pids()
    print(f"[WIN] Teams processes: {len(pids)}")
    for pid, name in pids.items():
        print(f"  PID {pid}: {name}")
    if not pids:
        return
    print("[WIN] Visible Teams windows:")
    rows = []

    def enum_handler(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return
        if pid not in pids:
            return
        rows.append((pid, win32gui.GetWindowText(hwnd)))

    win32gui.EnumWindows(enum_handler, None)
    for pid, title in rows:
        print(f"  PID {pid}: {title!r}")


def main():
    dump_windows()
    dump_mic_usage()
    print("\n--> Compare the [MIC] section inside and outside a meeting.")


if __name__ == "__main__":
    main()
