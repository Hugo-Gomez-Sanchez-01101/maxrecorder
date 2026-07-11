"""Punto de entrada de Max Recorder.

Lanza la aplicación (paquete maxrecorder). Lo usan Grabador.bat y la entrada
de inicio automático del registro de Windows; el argumento --tray arranca
minimizado en la bandeja del sistema.
"""

import os
import sys
import argparse
import faulthandler


def main():
    if sys.platform != "win32":
        print("Aviso: esta herramienta usa WASAPI loopback y solo funciona en Windows.")

    # Si se produce un cierre inesperado (crash nativo), este log guardará la
    # traza C/Python exacta para diagnosticarlo.
    try:
        crash_log = open(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "crash.log"),
            "a", buffering=1)
        faulthandler.enable(file=crash_log)
    except Exception:
        faulthandler.enable()

    parser = argparse.ArgumentParser(description="Max Recorder — Grabador de reuniones de Teams")
    parser.add_argument(
        "--tray", action="store_true",
        help="Arrancar minimizado en la bandeja con la detección de reuniones activada "
             "(lo usa el inicio automático al iniciar sesión).")
    args = parser.parse_args()

    from maxrecorder.ui.app import App
    app = App(start_in_tray=args.tray)
    app.mainloop()


if __name__ == "__main__":
    main()
