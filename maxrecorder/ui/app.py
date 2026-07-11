"""Ventana principal de Max Recorder."""

import os
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime

from ..audio import pyaudio, DualRecorder, save_wav_mono
from ..autostart import (WINREG_AVAILABLE, is_autostart_enabled,
                         enable_autostart, disable_autostart)
from ..config import (OUTPUT_DIR_DEFAULT, TRANSCRIPT_DIR_DEFAULT,
                      DEFAULT_MEETING_KEYWORDS, DEFAULT_TRANSCRIPT_PREFIX,
                      load_config, save_config)
from ..detection import (PSUTIL_AVAILABLE, WIN32_AVAILABLE,
                         MeetingWatcher, detect_meeting_prefix)
from ..transcription import (WHISPER_AVAILABLE, Transcriber,
                             format_ts, transcript_txt_path)
from .popup import MeetingPopup
from .settings import SettingsWindow
from .theme import (P, TechButton, StatusLED, AudioVisualizer, TechProgress,
                    make_section, dark_entry, dark_check, dark_label)

try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False


class App(tk.Tk):
    def __init__(self, start_in_tray=False):
        super().__init__()
        self.title("Max Recorder — Transcripción de reuniones de Teams")
        self.geometry("920x640")
        self.minsize(760, 520)
        self.configure(bg=P.BG)

        self.recorder = None
        self.recording = False
        self.transcribing = False
        self.last_paths = None       # dict(mixed=, sys=, mic=, txt=) de la última grabación
        self.transcript_text = ""
        self.meeting_watcher = None
        self.tray_icon = None
        self._quit_after_save = False
        self._rec_blink = False
        self.transcriber = Transcriber()

        cfg = load_config()
        self.output_dir = tk.StringVar(value=cfg.get("output_dir", OUTPUT_DIR_DEFAULT))
        self.transcript_dir = tk.StringVar(value=cfg.get("transcript_dir", TRANSCRIPT_DIR_DEFAULT))
        # La detección de reuniones va siempre activa: se arranca sola al
        # abrir la app (haya o no modo bandeja) y la casilla se deshabilita
        # para que no se pueda dejar desactivada por error.
        self.auto_detect_var = tk.BooleanVar(value=True)
        self.keywords_var = tk.StringVar(
            value=cfg.get("keywords", ", ".join(DEFAULT_MEETING_KEYWORDS)))
        try:
            poll = int(cfg.get("poll_interval", 4))
        except (TypeError, ValueError):
            poll = 4
        self.poll_interval_var = tk.IntVar(value=min(max(poll, 2), 30))
        self.autostart_var = tk.BooleanVar(value=is_autostart_enabled())
        self.settings_window = None
        self.speakers_var = tk.BooleanVar(value=True)     # modo Tú/Ellos
        self.timestamps_var = tk.BooleanVar(value=True)   # marcas de tiempo
        self.auto_transcribe_var = tk.BooleanVar(value=True)

        self._setup_style()
        self._build_ui()
        self._refresh_mic_list()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if TRAY_AVAILABLE:
            self._setup_tray()

        # La detección arranca siempre, independientemente del modo bandeja.
        self.after(0, self._toggle_auto_detect)

        # Arranque en modo bandeja (inicio automático al iniciar sesión):
        # ocultar la ventana.
        if start_in_tray:
            self.after(0, self._start_in_tray)

    def _start_in_tray(self):
        if TRAY_AVAILABLE:
            self.withdraw()

    # ---------------- Estilo ----------------

    def _setup_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TCombobox",
                        fieldbackground=P.FIELD, background=P.PANEL2,
                        foreground=P.TEXT, arrowcolor=P.ACCENT,
                        bordercolor=P.BORDER, lightcolor=P.PANEL,
                        darkcolor=P.PANEL, selectbackground=P.ACCENT_DK,
                        selectforeground=P.TEXT)
        style.map("TCombobox",
                  fieldbackground=[("readonly", P.FIELD)],
                  foreground=[("readonly", P.TEXT)])
        # desplegable del combobox
        self.option_add("*TCombobox*Listbox.background", P.FIELD)
        self.option_add("*TCombobox*Listbox.foreground", P.TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", P.ACCENT_DK)
        self.option_add("*TCombobox*Listbox.selectForeground", P.TEXT)
        style.configure("Vertical.TScrollbar",
                        background=P.PANEL2, troughcolor=P.FIELD,
                        bordercolor=P.BORDER, arrowcolor=P.DIM)

    # ---------------- Construcción de la UI ----------------

    def _build_ui(self):
        # ---- Cabecera ----
        header = tk.Frame(self, bg=P.BG)
        header.pack(fill="x", padx=10, pady=(10, 0))
        tk.Label(header, text="◉ MAX RECORDER", bg=P.BG, fg=P.ACCENT,
                 font=P.TITLE, anchor="w").pack(side="left")
        # Botones de la esquina superior derecha: pasar a segundo plano
        # y abrir la ventana de ajustes.
        TechButton(header, text="▾ SEGUNDO PLANO",
                   command=self._minimize_to_tray).pack(side="right")
        TechButton(header, text="⚙ AJUSTES",
                   command=self._open_settings).pack(side="right", padx=6)
        led_box = tk.Frame(header, bg=P.BG)
        led_box.pack(side="right", padx=(0, 8))
        self.led = StatusLED(led_box, bg=P.BG)
        self.led.set_state("ready")
        self.led.pack(side="left", padx=(0, 6))
        self.lbl_status = tk.Label(led_box, text="LISTO", bg=P.BG, fg=P.DIM,
                                   font=("Consolas", 9))
        self.lbl_status.pack(side="left")

        # ---- Visualizador ----
        self.visualizer = AudioVisualizer(self)
        self.visualizer.pack(fill="x", padx=10, pady=(8, 0))
        self.visualizer.level_source = self._get_levels

        # ---- Grabación ----
        _, rec = make_section(self, "Grabación")
        row = tk.Frame(rec, bg=P.PANEL)
        row.pack(fill="x", pady=2)
        self.btn_start = TechButton(row, kind="primary", text="●  INICIAR",
                                    command=self._start_recording, width=13)
        self.btn_start.pack(side="left", padx=(2, 6))
        self.btn_stop = TechButton(row, kind="danger", text="■  DETENER",
                                   command=self._stop_recording, width=13,
                                   state="disabled")
        self.btn_stop.pack(side="left", padx=6)
        self.lbl_rec = tk.Label(row, text="", bg=P.PANEL, fg=P.RED,
                                font=("Consolas", 10, "bold"), width=6)
        self.lbl_rec.pack(side="left", padx=(14, 0))
        self.lbl_timer = tk.Label(row, text="00:00:00", bg=P.PANEL, fg=P.TEXT,
                                  font=P.MONO_BIG)
        self.lbl_timer.pack(side="left", padx=6)

        row2 = tk.Frame(rec, bg=P.PANEL)
        row2.pack(fill="x", pady=(6, 2))
        dark_label(row2, text="Micrófono:").pack(side="left", padx=(2, 4))
        self.mic_combo = ttk.Combobox(row2, state="readonly", width=44)
        self.mic_combo.pack(side="left", padx=4, fill="x", expand=True)
        TechButton(row2, text="⟳", command=self._refresh_mic_list, width=3).pack(side="left", padx=4)
        dark_label(row2, dim=True,
                   text="(el audio del sistema se captura solo, vía WASAPI loopback)").pack(
            side="left", padx=6)

        row3 = tk.Frame(rec, bg=P.PANEL)
        row3.pack(fill="x", pady=(4, 2))
        dark_label(row3, text="Carpeta de salida:").pack(side="left", padx=(2, 4))
        dark_entry(row3, textvariable=self.output_dir).pack(
            side="left", padx=4, fill="x", expand=True, ipady=3)
        TechButton(row3, text="ELEGIR...", command=self._choose_output_dir).pack(
            side="left", padx=4)

        # ---- Transcripción ----
        outer_tr, tr = make_section(self, "Transcripción · faster-whisper (local)")
        outer_tr.pack_configure(fill="both", expand=True, pady=(8, 10))

        ctr = tk.Frame(tr, bg=P.PANEL)
        ctr.pack(fill="x", pady=2)
        dark_label(ctr, text="Modelo:").pack(side="left", padx=(2, 4))
        self.whisper_model_combo = ttk.Combobox(
            ctr, state="readonly", width=10,
            values=["tiny", "base", "small", "medium", "large-v3"])
        self.whisper_model_combo.set("small")
        self.whisper_model_combo.pack(side="left", padx=4)
        dark_label(ctr, text="Idioma:").pack(side="left", padx=(10, 4))
        self.lang_entry = dark_entry(ctr, width=5)
        self.lang_entry.insert(0, "es")
        self.lang_entry.pack(side="left", padx=4, ipady=3)
        dark_check(ctr, text="Tú / Ellos", variable=self.speakers_var).pack(side="left", padx=(12, 0))
        dark_check(ctr, text="Marcas de tiempo", variable=self.timestamps_var).pack(side="left", padx=(8, 0))
        dark_check(ctr, text="Auto al detener", variable=self.auto_transcribe_var).pack(side="left", padx=(8, 0))

        ctr2 = tk.Frame(tr, bg=P.PANEL)
        ctr2.pack(fill="x", pady=(4, 2))
        self.btn_transcribe = TechButton(
            ctr2, kind="primary", text="▶  TRANSCRIBIR ÚLTIMA",
            command=self._transcribe, state="disabled")
        self.btn_transcribe.pack(side="left", padx=2)
        TechButton(ctr2, text="ARCHIVO...", command=self._transcribe_file).pack(side="left", padx=6)
        TechButton(ctr2, text="GUARDAR .TXT", command=self._save_transcript).pack(side="left", padx=6)
        self.lbl_tr_status = dark_label(ctr2, dim=True, text="")
        self.lbl_tr_status.pack(side="left", padx=10)

        self.progress = TechProgress(tr)
        self.progress.pack(fill="x", pady=(6, 4))

        txt_frame = tk.Frame(tr, bg=P.PANEL)
        txt_frame.pack(fill="both", expand=True)
        self.txt_transcript = tk.Text(
            txt_frame, height=10, wrap="word", bg=P.FIELD, fg=P.TEXT,
            insertbackground=P.ACCENT, relief="flat", font=("Consolas", 10),
            highlightthickness=1, highlightbackground=P.BORDER,
            selectbackground=P.ACCENT_DK, padx=8, pady=6)
        sb = ttk.Scrollbar(txt_frame, orient="vertical", command=self.txt_transcript.yview)
        self.txt_transcript.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt_transcript.pack(side="left", fill="both", expand=True)
        # colores para las etiquetas de hablante y timestamps
        self.txt_transcript.tag_configure("ts", foreground=P.DIM)
        self.txt_transcript.tag_configure("me", foreground=P.GREEN)
        self.txt_transcript.tag_configure("them", foreground=P.ACCENT)

    def _set_status(self, text, led_state=None):
        # Corto: comparte la cabecera con el subtítulo y los botones.
        self.lbl_status.config(text=text.upper()[:34])
        if led_state:
            self.led.set_state(led_state)

    def _get_levels(self):
        if self.recorder is not None and self.recording:
            return self.recorder.get_levels()
        return 0.0, 0.0

    # ---------------- Carpeta / dispositivos ----------------

    def _choose_output_dir(self):
        d = filedialog.askdirectory()
        if d:
            self.output_dir.set(d)

    def _refresh_mic_list(self):
        if pyaudio is None:
            self.mic_combo["values"] = ["PyAudioWPatch no instalado"]
            return
        try:
            p = pyaudio.PyAudio()
            devices = []
            for i in range(p.get_device_count()):
                info = p.get_device_info_by_index(i)
                if info.get("maxInputChannels", 0) > 0 and not info.get("isLoopbackDevice", False):
                    devices.append(f"{i}: {info['name']}")
            p.terminate()
            self.mic_combo["values"] = devices
            if devices:
                self.mic_combo.current(0)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudieron listar dispositivos:\n{e}")

    # ---------------- Grabación ----------------

    def _start_recording(self):
        if self.recording:
            return
        if pyaudio is None:
            messagebox.showerror(
                "Falta dependencia",
                "Instala PyAudioWPatch (solo Windows):\npip install PyAudioWPatch")
            return
        mic_sel = self.mic_combo.get()
        mic_index = int(mic_sel.split(":")[0]) if mic_sel and ":" in mic_sel else None
        try:
            self.recorder = DualRecorder(mic_device_index=mic_index)
            self.recorder.start()
        except Exception as e:
            messagebox.showerror("Error al iniciar grabación", str(e))
            return

        self.recording = True
        # El nombre del .txt depende de la reunión en curso: se mira el título
        # de las ventanas de Teams AHORA (al detener podría estar ya cerrada).
        self._meeting_prefix = detect_meeting_prefix()
        self.visualizer.recording = True
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.btn_transcribe.config(state="disabled")
        self._set_status("Grabando sistema + micrófono", "recording")
        self.deiconify()
        self._tick_timer()
        self._blink_rec()

    def _blink_rec(self):
        if not self.recording:
            self.lbl_rec.config(text="")
            return
        self._rec_blink = not self._rec_blink
        self.lbl_rec.config(text="● REC" if self._rec_blink else "  REC")
        self.after(600, self._blink_rec)

    def _tick_timer(self):
        if not self.recording or self.recorder is None:
            return
        errors = self.recorder.get_errors()
        if errors:
            messagebox.showerror("Error durante la grabación", "\n".join(errors))
            self._stop_recording()
            return
        secs = int(self.recorder.elapsed_seconds())
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        self.lbl_timer.config(text=f"{h:02d}:{m:02d}:{s:02d}")
        self.after(500, self._tick_timer)

    def _stop_recording(self):
        if not self.recorder or not self.recording:
            return
        # Detenemos el timer y deshabilitamos el botón inmediatamente. El
        # procesado (alineado + mezcla + guardado en disco) es pesado y puede
        # tardar bastante en grabaciones largas, así que lo hacemos en un hilo
        # aparte para no congelar (ni tumbar) la interfaz.
        self.recording = False
        self.visualizer.recording = False
        self.btn_stop.config(state="disabled")
        self._set_status("Procesando y alineando pistas...", "busy")
        output_dir = self.output_dir.get()
        transcript_dir = self.transcript_dir.get().strip() or output_dir
        prefix = getattr(self, "_meeting_prefix", DEFAULT_TRANSCRIPT_PREFIX)
        recorder = self.recorder
        self.recorder = None
        threading.Thread(
            target=self._stop_worker,
            args=(recorder, output_dir, transcript_dir, prefix), daemon=True).start()

    def _stop_worker(self, recorder, output_dir, transcript_dir, prefix):
        try:
            mixed, rate, sys_only, mic_only = recorder.stop_and_mix()
            errors = recorder.get_errors()
            recorder.close()

            os.makedirs(output_dir, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            paths = {
                "mixed": os.path.join(output_dir, f"reunion_{stamp}.wav"),
                "sys": os.path.join(output_dir, f"reunion_{stamp}_sistema.wav"),
                "mic": os.path.join(output_dir, f"reunion_{stamp}_mic.wav"),
                "txt": transcript_txt_path(transcript_dir, prefix),
            }

            save_wav_mono(paths["mixed"], mixed, rate)
            save_wav_mono(paths["sys"], sys_only, rate)
            save_wav_mono(paths["mic"], mic_only, rate)

            self.after(0, lambda: self._on_stop_done(paths, errors))
        except Exception as e:
            try:
                recorder.close()
            except Exception:
                pass
            self.after(0, lambda: self._on_stop_error(e))

    def _on_stop_done(self, paths, errors):
        self.btn_start.config(state="normal")
        self.btn_transcribe.config(state="normal")
        self.last_paths = paths
        self._set_status(f"Guardado: {os.path.basename(paths['mixed'])}", "ready")
        if errors:
            messagebox.showwarning("Avisos durante la grabación", "\n".join(errors))
        if self._quit_after_save:
            self._quit_after_save = False
            self._quit_app()
            return
        if self.auto_transcribe_var.get() and WHISPER_AVAILABLE:
            self._transcribe()

    def _on_stop_error(self, error):
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self._set_status("Error al procesar la grabación", "idle")
        messagebox.showerror("Error al detener la grabación", str(error))
        if self._quit_after_save:
            # Falló el guardado; preguntamos si aun así quiere cerrar.
            self._quit_after_save = False
            if messagebox.askyesno("Cerrar", "No se pudo guardar la grabación.\n¿Cerrar de todos modos?"):
                self._quit_app()

    # ---------------- Detección de reuniones ----------------

    def _toggle_auto_detect(self):
        if self.auto_detect_var.get():
            if not (PSUTIL_AVAILABLE and WIN32_AVAILABLE):
                # La detección es siempre activa por diseño; si faltan
                # dependencias solo lo reflejamos en el estado (el aviso
                # ya está visible en la ventana de ajustes) en vez de
                # interrumpir el arranque con un diálogo.
                self.auto_detect_var.set(False)
                self._set_status("Detección no disponible (faltan dependencias)", "idle")
                return
            keywords = [k for k in self.keywords_var.get().split(",")]
            self.meeting_watcher = MeetingWatcher(
                on_meeting_start=self._on_meeting_detected,
                on_meeting_end=None,
                keywords=keywords,
                poll_interval=self.poll_interval_var.get(),
            )
            self.meeting_watcher.start()
            self._set_status("Vigilando Teams...", "watching")
        else:
            if self.meeting_watcher:
                self.meeting_watcher.stop()
                self.meeting_watcher = None
            self._set_status("Detección desactivada", "ready")

    def _on_meeting_detected(self):
        # Llamado desde el hilo del watcher: hay que saltar al hilo de Tkinter.
        self.after(0, self._show_meeting_popup)

    def _show_meeting_popup(self):
        if self.recording:
            return  # ya está grabando, no molestar
        MeetingPopup(self, on_accept=self._start_recording)

    def _test_popup(self):
        self._show_meeting_popup()

    # ---------------- Ajustes ----------------

    def _open_settings(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.lift()
            self.settings_window.focus_set()
            return
        self.settings_window = SettingsWindow(self)

    def _apply_settings(self):
        """Persiste los ajustes y los aplica al watcher en marcha."""
        try:
            poll = min(max(int(self.poll_interval_var.get()), 2), 30)
        except (tk.TclError, ValueError):
            poll = 4
        self.poll_interval_var.set(poll)
        save_config({
            "output_dir": self.output_dir.get().strip(),
            "transcript_dir": self.transcript_dir.get().strip(),
            "keywords": self.keywords_var.get(),
            "poll_interval": poll,
        })
        if self.meeting_watcher:
            self.meeting_watcher.update_keywords(self.keywords_var.get().split(","))
            self.meeting_watcher.poll_interval = poll

    # ---------------- Inicio automático ----------------

    def _toggle_autostart(self):
        if not WINREG_AVAILABLE:
            messagebox.showerror("No disponible", "El inicio automático solo está disponible en Windows.")
            self.autostart_var.set(False)
            return
        try:
            if self.autostart_var.get():
                enable_autostart()
                self._set_status("Inicio automático activado", "ready")
            else:
                disable_autostart()
                self._set_status("Inicio automático desactivado", "ready")
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo cambiar el inicio automático:\n{e}")
            self.autostart_var.set(is_autostart_enabled())

    # ---------------- Bandeja del sistema ----------------

    def _setup_tray(self):
        image = self._make_tray_image()
        menu = pystray.Menu(
            pystray.MenuItem("Abrir", self._tray_open, default=True),
            pystray.MenuItem("Iniciar grabación", lambda: self.after(0, self._start_recording)),
            pystray.MenuItem("Detener grabación", lambda: self.after(0, self._stop_recording)),
            pystray.MenuItem("Salir", self._tray_quit),
        )
        self.tray_icon = pystray.Icon("max_recorder", image, "Max Recorder", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _make_tray_image(self):
        img = Image.new("RGB", (64, 64), "#0a0e13")
        d = ImageDraw.Draw(img)
        d.ellipse((8, 8, 56, 56), outline="#00e5ff", width=4)
        d.ellipse((22, 22, 42, 42), fill="#ff3860")
        return img

    def _tray_open(self, icon=None, item=None):
        self.after(0, self.deiconify)

    def _tray_quit(self, icon=None, item=None):
        self.after(0, self._quit_app)

    def _minimize_to_tray(self):
        if not TRAY_AVAILABLE:
            messagebox.showerror("Falta dependencia", "Instala: pip install pystray pillow")
            return
        self.withdraw()

    def _on_close(self):
        if TRAY_AVAILABLE:
            if messagebox.askyesno(
                    "Minimizar",
                    "¿Minimizar a la bandeja del sistema y seguir vigilando reuniones?\n"
                    "(No = cerrar la aplicación por completo)"):
                self.withdraw()
                return
        self._quit_app()

    def _quit_app(self):
        # Aviso si hay una grabación en curso: evita perderla al cerrar.
        if self.recording:
            resp = messagebox.askyesnocancel(
                "Grabación en curso",
                "Hay una grabación en curso.\n\n"
                "- Sí: detenerla y guardarla antes de salir.\n"
                "- No: descartarla y salir (se pierde).\n"
                "- Cancelar: no cerrar.",
                icon="warning")
            if resp is None:
                return  # Cancelar: no cerramos
            if resp:
                # Guardar y salir: detenemos (guarda en segundo plano) y cerramos
                # cuando termine, desde _on_stop_done / _on_stop_error.
                self._quit_after_save = True
                self._set_status("Guardando antes de salir...", "busy")
                self._stop_recording()
                return
            # No: descartar la grabación en curso sin guardar.
            self.recording = False
            if self.recorder:
                try:
                    self.recorder.close()
                except Exception:
                    pass
                self.recorder = None

        self._apply_settings()
        if self.meeting_watcher:
            self.meeting_watcher.stop()
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
        self.destroy()

    # ---------------- Transcripción ----------------

    def _transcribe(self):
        """Transcribe la última grabación. Si está activado 'Tú / Ellos' y
        existen las pistas separadas, transcribe cada una y entrelaza los
        segmentos con etiqueta de hablante."""
        if not self._check_whisper():
            return
        if not self.last_paths or not os.path.exists(self.last_paths["mixed"]):
            messagebox.showwarning("Aviso", "Primero graba y detén una grabación.")
            return

        jobs = None
        if self.speakers_var.get():
            sys_p, mic_p = self.last_paths.get("sys"), self.last_paths.get("mic")
            if sys_p and mic_p and os.path.exists(sys_p) and os.path.exists(mic_p):
                jobs = [("Ellos", sys_p), ("Tú", mic_p)]
        if jobs is None:
            jobs = [(None, self.last_paths["mixed"])]

        # Ruta calculada al detener la grabación (reunion_/weekly_AAAA-MM-DD.txt
        # en la carpeta de transcripciones); re-transcribir sobreescribe el mismo.
        autosave = self.last_paths.get("txt")
        if not autosave:
            autosave = transcript_txt_path(
                self.transcript_dir.get().strip() or self.output_dir.get(),
                DEFAULT_TRANSCRIPT_PREFIX)
            self.last_paths["txt"] = autosave
        self._run_transcription(jobs, autosave_path=autosave)

    def _transcribe_file(self):
        """Transcribe un archivo de audio cualquiera elegido por el usuario."""
        if not self._check_whisper():
            return
        path = filedialog.askopenfilename(
            initialdir=self.output_dir.get(),
            filetypes=[("Audio", "*.wav *.mp3 *.m4a *.flac *.ogg *.opus"), ("Todos", "*.*")])
        if not path:
            return
        base = os.path.splitext(path)[0]
        self._run_transcription([(None, path)], autosave_path=base + "_transcripcion.txt")

    def _check_whisper(self):
        if not WHISPER_AVAILABLE:
            messagebox.showerror(
                "Falta dependencia",
                "Instala faster-whisper:\npip install faster-whisper")
            return False
        if self.transcribing:
            messagebox.showinfo("Transcripción", "Ya hay una transcripción en curso.")
            return False
        return True

    def _run_transcription(self, jobs, autosave_path=None):
        self.transcribing = True
        self.btn_transcribe.config(state="disabled")
        self.txt_transcript.delete("1.0", tk.END)
        self.progress.set(None)  # indeterminada mientras carga el modelo
        self._set_status("Transcribiendo...", "busy")
        model_size = self.whisper_model_combo.get()
        lang = self.lang_entry.get().strip() or None
        threading.Thread(
            target=self._transcribe_worker,
            args=(jobs, model_size, lang, autosave_path), daemon=True).start()

    def _transcribe_worker(self, jobs, model_size, lang, autosave_path):
        try:
            segs = self.transcriber.transcribe_jobs(
                jobs, model_size, language=lang,
                on_segment=lambda s, l, t: self.after(0, self._append_segment, s, l, t),
                on_progress=lambda f: self.after(0, self.progress.set, f),
                on_phase=lambda msg: self.after(0, self.lbl_tr_status.config, {"text": msg}),
            )
            multi = len(jobs) > 1
            text = self.transcriber.format_segments(
                segs, with_timestamps=self.timestamps_var.get())
            self.transcript_text = text

            saved = None
            if autosave_path and text.strip():
                try:
                    os.makedirs(os.path.dirname(autosave_path) or ".", exist_ok=True)
                    with open(autosave_path, "w", encoding="utf-8") as f:
                        f.write(text)
                    saved = autosave_path
                except OSError:
                    pass

            self.after(0, lambda: self._on_transcribe_done(segs, multi, saved))
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("Error en transcripción", str(e)))
            self.after(0, lambda: self._set_status("Error al transcribir", "idle"))
            self.after(0, self.progress.hide)
        finally:
            self.transcribing = False
            self.after(0, lambda: self.btn_transcribe.config(
                state="normal" if self.last_paths else "disabled"))

    def _append_segment(self, start, label, text):
        """Streaming: añade un segmento al panel según se va transcribiendo."""
        if self.timestamps_var.get():
            self.txt_transcript.insert(tk.END, f"[{format_ts(start)}] ", "ts")
        if label:
            tag = "me" if label == "Tú" else "them"
            self.txt_transcript.insert(tk.END, f"{label}: ", tag)
        self.txt_transcript.insert(tk.END, text + "\n")
        self.txt_transcript.see(tk.END)

    def _on_transcribe_done(self, segs, multi, saved_path):
        # Con varias pistas los segmentos llegaron intercalados por pista;
        # reescribimos el panel ya ordenado por tiempo.
        if multi:
            self.txt_transcript.delete("1.0", tk.END)
            for start, label, text in segs:
                self._append_segment(start, label, text)
        self.progress.set(1.0)
        n = len(segs)
        msg = f"{n} segmentos"
        if saved_path:
            msg += f" · guardado {os.path.basename(saved_path)}"
        self.lbl_tr_status.config(text=msg)
        self._set_status("Transcripción completada", "ready")

    def _save_transcript(self):
        text = self.txt_transcript.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("Aviso", "No hay transcripción que guardar.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt", initialdir=self.transcript_dir.get() or self.output_dir.get(),
            initialfile="transcripcion.txt")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
