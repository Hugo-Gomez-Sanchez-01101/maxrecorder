"""
Diagnóstico de detección de reuniones de Teams.

1) Lista las ventanas visibles de Teams (método por título, poco fiable).
2) Vuelca el uso ACTUAL del micrófono por app según el registro de Windows
   (CapabilityAccessManager). Cuando una app está usando el micro AHORA mismo,
   su valor 'LastUsedTimeStop' es 0. Esta es la señal robusta para saber si
   Teams está en una llamada/reunión, sin depender del título de la ventana.

Uso:  python diag_teams.py
Ejecútalo una vez FUERA de reunión y otra DENTRO, y compara la sección [MIC].
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
    """Genera (app_name, LastUsedTimeStart, LastUsedTimeStop) de cada subclave."""
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
    print("\n[MIC] Uso del micrófono por app (Stop==0 => usándolo AHORA):")
    rows = []
    # Apps empaquetadas (el nuevo Teams es MSIX: 'MSTeams_8wekyb3d8bbwe')
    for name, start, stop in _iter_consent_apps(winreg.HKEY_CURRENT_USER, MIC_CONSENT_KEY):
        if name.lower() == "nonpackaged":
            continue
        rows.append((name, start, stop))
    # Apps no empaquetadas (Teams clásico)
    for name, start, stop in _iter_consent_apps(
            winreg.HKEY_CURRENT_USER, MIC_CONSENT_KEY + r"\NonPackaged"):
        rows.append((name, start, stop))

    if not rows:
        print("  (sin entradas; puede que ninguna app haya usado el micro)")
    for name, start, stop in rows:
        in_use = "  <<< EN USO AHORA" if stop == 0 else ""
        is_teams = "teams" in name.lower()
        mark = " [TEAMS]" if is_teams else ""
        print(f"  {name}{mark}: start={start} stop={stop}{in_use}")


def dump_windows():
    pids = teams_pids()
    print(f"[WIN] Procesos de Teams: {len(pids)}")
    for pid, name in pids.items():
        print(f"  PID {pid}: {name}")
    if not pids:
        return
    print("[WIN] Ventanas visibles de Teams:")
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
    print("\n--> Compara la seccion [MIC] dentro y fuera de reunion.")


if __name__ == "__main__":
    main()
