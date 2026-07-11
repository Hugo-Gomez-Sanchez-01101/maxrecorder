"""Ventana de ajustes: carpeta por defecto de transcripciones y opciones de
segundo plano (detección de reuniones, palabras clave, sondeo, arranque al
iniciar Windows, probar el aviso). Se guardan en config.json al cerrar."""

import tkinter as tk
from tkinter import filedialog

from ..autostart import WINREG_AVAILABLE
from ..detection import PSUTIL_AVAILABLE, WIN32_AVAILABLE
from .theme import P, TechButton, make_section, dark_entry, dark_check, dark_label

try:
    import pystray  # noqa: F401
    from PIL import Image  # noqa: F401
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False


class SettingsWindow(tk.Toplevel):

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Ajustes — Max Recorder")
        self.configure(bg=P.BG)
        self.resizable(False, False)
        self.transient(app)
        self.geometry(f"+{app.winfo_rootx() + 90}+{app.winfo_rooty() + 70}")
        self.protocol("WM_DELETE_WINDOW", self._save_close)

        tk.Label(self, text="AJUSTES", bg=P.BG, fg=P.ACCENT,
                 font=("Consolas", 12, "bold"), anchor="w").pack(
            fill="x", padx=12, pady=(10, 0))

        # ---- Transcripciones ----
        _, tr = make_section(self, "Transcripciones")
        row = tk.Frame(tr, bg=P.PANEL)
        row.pack(fill="x", pady=2)
        dark_label(row, text="Carpeta por defecto:").pack(side="left", padx=(2, 4))
        dark_entry(row, textvariable=app.transcript_dir, width=45).pack(
            side="left", padx=4, fill="x", expand=True, ipady=3)
        TechButton(row, text="ELEGIR...", command=self._choose_dir).pack(side="left", padx=4)

        # ---- Segundo plano ----
        _, bg_sec = make_section(self, "Segundo plano · Detección de reuniones")
        row1 = tk.Frame(bg_sec, bg=P.PANEL)
        row1.pack(fill="x", pady=2)
        dark_check(row1, text="Detectar reuniones automáticamente y avisar (siempre activa)",
                   variable=app.auto_detect_var, state="disabled",
                   disabledforeground=P.TEXT).pack(side="left")
        dark_label(row1, text="Sondeo (s):").pack(side="left", padx=(16, 4))
        tk.Spinbox(row1, from_=2, to=30, width=4, textvariable=app.poll_interval_var,
                   bg=P.FIELD, fg=P.TEXT, buttonbackground=P.PANEL2,
                   insertbackground=P.ACCENT, relief="flat",
                   highlightthickness=1, highlightbackground=P.BORDER).pack(side="left")

        row2 = tk.Frame(bg_sec, bg=P.PANEL)
        row2.pack(fill="x", pady=2)
        dark_label(row2, text="Palabras clave (respaldo por título):").pack(side="left", padx=(2, 4))
        dark_entry(row2, textvariable=app.keywords_var).pack(
            side="left", padx=4, fill="x", expand=True, ipady=3)

        row3 = tk.Frame(bg_sec, bg=P.PANEL)
        row3.pack(fill="x", pady=2)
        dark_check(row3,
                   text="Arrancar automáticamente al iniciar sesión de Windows (en segundo plano)",
                   variable=app.autostart_var, command=app._toggle_autostart).pack(side="left")
        if not WINREG_AVAILABLE:
            lbl_no_reg = dark_label(row3, text="(no disponible en esta plataforma)")
            lbl_no_reg.config(fg=P.RED)
            lbl_no_reg.pack(side="left", padx=6)

        row4 = tk.Frame(bg_sec, bg=P.PANEL)
        row4.pack(fill="x", pady=(4, 2))
        TechButton(row4, text="PROBAR AVISO", command=app._test_popup).pack(side="left", padx=2)

        if not (PSUTIL_AVAILABLE and WIN32_AVAILABLE):
            tk.Label(bg_sec, text="Faltan dependencias para la detección: pip install psutil pywin32",
                     bg=P.PANEL, fg=P.AMBER, font=P.FONT_SM, anchor="w").pack(fill="x")
        if not TRAY_AVAILABLE:
            tk.Label(bg_sec, text="Para el segundo plano instala: pip install pystray pillow",
                     bg=P.PANEL, fg=P.AMBER, font=P.FONT_SM, anchor="w").pack(fill="x")

        # ---- Cierre ----
        btns = tk.Frame(self, bg=P.BG)
        btns.pack(fill="x", padx=12, pady=10)
        TechButton(btns, kind="primary", text="GUARDAR Y CERRAR",
                   command=self._save_close).pack(side="right")

    def _choose_dir(self):
        d = filedialog.askdirectory(
            initialdir=self.app.transcript_dir.get() or self.app.output_dir.get(),
            parent=self)
        if d:
            self.app.transcript_dir.set(d)

    def _save_close(self):
        self.app._apply_settings()
        self.destroy()
