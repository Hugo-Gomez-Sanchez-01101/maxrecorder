"""Max Recorder entry point.

Launches the application (maxrecorder package). Used by Grabador.bat and the
Windows registry autostart entry; the --tray argument starts minimized in the
system tray.
"""

import os
import sys
import argparse
import faulthandler


def main():
    if sys.platform != "win32":
        print("Warning: this tool uses WASAPI loopback and only works on Windows.")
    else:
        # Without this, Windows groups the process under the interpreter's
        # default AppUserModelID (python.exe/pythonw.exe) and the taskbar
        # shows the generic Python icon instead of the window's, even if it
        # was set with iconbitmap. It must be called BEFORE creating any
        # window.
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "MaxRecorder.App")
        except Exception:
            pass

    # If an unexpected shutdown happens (native crash), this log will store the
    # exact C/Python traceback to diagnose it.
    try:
        crash_log = open(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "crash.log"),
            "a", buffering=1)
        faulthandler.enable(file=crash_log)
    except Exception:
        faulthandler.enable()

    parser = argparse.ArgumentParser(description="Max Recorder — Teams meeting recorder")
    parser.add_argument(
        "--tray", action="store_true",
        help="Start minimized in the tray with meeting detection enabled "
             "(used by autostart at login).")
    args = parser.parse_args()

    from maxrecorder.ui.app import App
    app = App(start_in_tray=args.tray)
    app.mainloop()


if __name__ == "__main__":
    main()
