"""Max Recorder main window."""

import os
import shutil
import tempfile
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from datetime import datetime

from ..audio import pyaudio, DualRecorder, save_wav_mono
from ..autostart import (WINREG_AVAILABLE, is_autostart_enabled,
                         enable_autostart, disable_autostart,
                         refresh_autostart_if_enabled)
from ..config import (RECORD_DIR_DEFAULT, TRANSCRIPT_DIR_DEFAULT,
                      DEFAULT_MEETING_KEYWORDS, DEFAULT_TRANSCRIPT_PREFIX,
                      load_config, save_config)
from ..detection import (PSUTIL_AVAILABLE, WIN32_AVAILABLE,
                         MeetingWatcher, detect_meeting_prefix)
from ..transcription import (WHISPER_AVAILABLE, Transcriber,
                             format_ts, transcript_txt_path)
from .popup import MeetingPopup
from .settings import SettingsWindow
from .theme import (P, set_theme, DEFAULT_THEME, TechButton, StatusLED,
                    AudioVisualizer, TechProgress,
                    make_section, dark_entry, dark_check, dark_label)

try:
    import pystray
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False

try:
    import win32gui
    import win32con
    WIN32_ICON_AVAILABLE = True
except ImportError:
    WIN32_ICON_AVAILABLE = False


ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "icon.ico")

# Speaker labels used in the "You / Them" mode.
LABEL_ME = "You"
LABEL_THEM = "Them"


class App(tk.Tk):
    def __init__(self, start_in_tray=False):
        super().__init__()
        # Load the config (and with it the theme) BEFORE building any widget:
        # colors are read from P at construction time.
        cfg = load_config()
        self.theme_name = cfg.get("theme", DEFAULT_THEME)
        set_theme(self.theme_name)
        self.theme_name = P.NAME  # normalized if the value was unknown

        self.title("Max Recorder — Teams meeting transcription")
        self.geometry("920x640")
        self.minsize(760, 520)
        self.configure(bg=P.BG)
        # Title-bar / alt-tab icon via Tk.
        try:
            self.iconbitmap(default=ICON_PATH)
        except tk.TclError:
            pass
        # The Windows taskbar icon has to be set separately: the Tcl/Tk icon
        # loader does not correctly handle .ico files with PNG-compressed
        # frames (the ones Pillow generates), so iconbitmap does not fail but
        # does not change the taskbar icon either. We set the HICON directly on
        # the real window via WM_SETICON, which does use the Windows icon
        # loader (it supports PNG inside .ico).
        self._apply_taskbar_icon()

        self.recorder = None
        self.recording = False
        self.transcribing = False
        self.last_paths = None       # dict(mixed=, sys=, mic=, txt=) of the last recording
        self.transcript_text = ""
        self.meeting_watcher = None
        self.tray_icon = None
        self._quit_after_save = False
        self._rec_blink = False
        self.transcriber = Transcriber()
        # Temporary folder for the microphone and system tracks. Only the final
        # mix is saved to disk (in the recordings folder); the separate tracks
        # are written here so the "You / Them" mode can transcribe them, and
        # they are deleted when the app closes.
        self._temp_dir = None

        self.record_dir = tk.StringVar(value=cfg.get("record_dir", RECORD_DIR_DEFAULT))
        self.transcript_dir = tk.StringVar(value=cfg.get("transcript_dir", TRANSCRIPT_DIR_DEFAULT))
        # Meeting detection is always on: it starts by itself when the app
        # opens (whether or not in tray mode) and the checkbox is disabled so
        # it cannot be left off by mistake.
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
        self.speakers_var = tk.BooleanVar(value=True)     # You/Them mode
        self.timestamps_var = tk.BooleanVar(value=True)   # timestamps
        self.auto_transcribe_var = tk.BooleanVar(value=True)

        # If autostart is enabled but the project folder was moved/renamed, the
        # registry entry points to a dead path; it is fixed here automatically.
        refresh_autostart_if_enabled()

        self._setup_style()
        self._build_ui()
        self._refresh_mic_list()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if TRAY_AVAILABLE:
            self._setup_tray()

        # Detection always starts, regardless of tray mode.
        self.after(0, self._toggle_auto_detect)

        # Tray-mode startup (autostart at login): hide the window.
        if start_in_tray:
            self.after(0, self._start_in_tray)

    def _start_in_tray(self):
        if TRAY_AVAILABLE:
            self.withdraw()

    def _apply_taskbar_icon(self):
        if not WIN32_ICON_AVAILABLE or not os.path.exists(ICON_PATH):
            return
        try:
            self.update_idletasks()
            hwnd = win32gui.GetParent(self.winfo_id())
            flags = win32con.LR_LOADFROMFILE
            hicon_small = win32gui.LoadImage(0, ICON_PATH, win32con.IMAGE_ICON, 16, 16, flags)
            hicon_big = win32gui.LoadImage(0, ICON_PATH, win32con.IMAGE_ICON, 32, 32, flags)
            win32gui.SendMessage(hwnd, win32con.WM_SETICON, win32con.ICON_SMALL, hicon_small)
            win32gui.SendMessage(hwnd, win32con.WM_SETICON, win32con.ICON_BIG, hicon_big)
        except Exception:
            pass

    # ---------------- Style ----------------

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
        # combobox dropdown
        self.option_add("*TCombobox*Listbox.background", P.FIELD)
        self.option_add("*TCombobox*Listbox.foreground", P.TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", P.ACCENT_DK)
        self.option_add("*TCombobox*Listbox.selectForeground", P.TEXT)
        style.configure("Vertical.TScrollbar",
                        background=P.PANEL2, troughcolor=P.FIELD,
                        bordercolor=P.BORDER, arrowcolor=P.DIM)

    # ---------------- UI construction ----------------

    def _build_ui(self):
        # ---- Header ----
        header = tk.Frame(self, bg=P.BG)
        header.pack(fill="x", padx=10, pady=(10, 0))
        tk.Label(header, text="◉ MAX RECORDER", bg=P.BG, fg=P.ACCENT,
                 font=P.TITLE, anchor="w").pack(side="left")
        # Top-right corner buttons: go to background and open the settings window.
        TechButton(header, text="▾ BACKGROUND",
                   command=self._minimize_to_tray).pack(side="right")
        TechButton(header, text="⚙ SETTINGS",
                   command=self._open_settings).pack(side="right", padx=6)
        led_box = tk.Frame(header, bg=P.BG)
        led_box.pack(side="right", padx=(0, 8))
        self.led = StatusLED(led_box, bg=P.BG)
        self.led.set_state("ready")
        self.led.pack(side="left", padx=(0, 6))
        self.lbl_status = tk.Label(led_box, text="READY", bg=P.BG, fg=P.DIM,
                                   font=("Consolas", 9))
        self.lbl_status.pack(side="left")

        # ---- Visualizer ----
        self.visualizer = AudioVisualizer(self)
        self.visualizer.pack(fill="x", padx=10, pady=(8, 0))
        self.visualizer.level_source = self._get_levels

        # ---- Recording ----
        _, rec = make_section(self, "Recording")
        row = tk.Frame(rec, bg=P.PANEL)
        row.pack(fill="x", pady=2)
        self.btn_start = TechButton(row, kind="primary", text="●  START",
                                    command=self._start_recording, width=13)
        self.btn_start.pack(side="left", padx=(2, 6))
        self.btn_stop = TechButton(row, kind="danger", text="■  STOP",
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
        dark_label(row2, text="Microphone:").pack(side="left", padx=(2, 4))
        self.mic_combo = ttk.Combobox(row2, state="readonly", width=44)
        self.mic_combo.pack(side="left", padx=4, fill="x", expand=True)
        TechButton(row2, text="⟳", command=self._refresh_mic_list, width=3).pack(side="left", padx=4)
        dark_label(row2, dim=True,
                   text="(system audio is captured automatically, via WASAPI loopback)").pack(
            side="left", padx=6)

        # (The recordings folder is configured in the Settings window.)

        # ---- Transcription ----
        outer_tr, tr = make_section(self, "Transcription · faster-whisper (local)")
        outer_tr.pack_configure(fill="both", expand=True, pady=(8, 10))

        ctr = tk.Frame(tr, bg=P.PANEL)
        ctr.pack(fill="x", pady=2)
        dark_label(ctr, text="Model:").pack(side="left", padx=(2, 4))
        self.whisper_model_combo = ttk.Combobox(
            ctr, state="readonly", width=10,
            values=["tiny", "base", "small", "medium", "large-v3"])
        self.whisper_model_combo.set("small")
        self.whisper_model_combo.pack(side="left", padx=4)
        dark_label(ctr, text="Language:").pack(side="left", padx=(10, 4))
        self.lang_entry = dark_entry(ctr, width=5)
        self.lang_entry.insert(0, "es")
        self.lang_entry.pack(side="left", padx=4, ipady=3)
        dark_check(ctr, text="You / Them", variable=self.speakers_var).pack(side="left", padx=(12, 0))
        dark_check(ctr, text="Timestamps", variable=self.timestamps_var).pack(side="left", padx=(8, 0))
        dark_check(ctr, text="Transcribe on stop", variable=self.auto_transcribe_var).pack(side="left", padx=(8, 0))

        ctr2 = tk.Frame(tr, bg=P.PANEL)
        ctr2.pack(fill="x", pady=(4, 2))
        self.btn_transcribe = TechButton(
            ctr2, kind="primary", text="▶  TRANSCRIBE LAST",
            command=self._transcribe, state="disabled")
        self.btn_transcribe.pack(side="left", padx=2)
        TechButton(ctr2, text="FILE...", command=self._transcribe_file).pack(side="left", padx=6)
        TechButton(ctr2, text="SAVE .TXT", command=self._save_transcript).pack(side="left", padx=6)
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
        # colors for the speaker labels and timestamps
        self.txt_transcript.tag_configure("ts", foreground=P.DIM)
        self.txt_transcript.tag_configure("me", foreground=P.GREEN)
        self.txt_transcript.tag_configure("them", foreground=P.ACCENT)

    def _set_status(self, text, led_state=None):
        # Short: it shares the header with the subtitle and the buttons.
        self.lbl_status.config(text=text.upper()[:34])
        if led_state:
            self.led.set_state(led_state)

    def _get_levels(self):
        if self.recorder is not None and self.recording:
            return self.recorder.get_levels()
        return 0.0, 0.0

    # ---------------- Devices ----------------

    def _refresh_mic_list(self):
        if pyaudio is None:
            self.mic_combo["values"] = ["PyAudioWPatch not installed"]
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
            messagebox.showerror("Error", f"Could not list devices:\n{e}")

    # ---------------- Recording ----------------

    def _start_recording(self):
        if self.recording:
            return
        if pyaudio is None:
            messagebox.showerror(
                "Missing dependency",
                "Install PyAudioWPatch (Windows only):\npip install PyAudioWPatch")
            return
        mic_sel = self.mic_combo.get()
        mic_index = int(mic_sel.split(":")[0]) if mic_sel and ":" in mic_sel else None
        try:
            self.recorder = DualRecorder(mic_device_index=mic_index)
            self.recorder.start()
        except Exception as e:
            messagebox.showerror("Error starting recording", str(e))
            return

        self.recording = True
        # The .txt name depends on the current meeting: we read the Teams
        # window titles NOW (by stop time the window might already be closed).
        self._meeting_prefix = detect_meeting_prefix()
        self.visualizer.recording = True
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.btn_transcribe.config(state="disabled")
        self._set_status("Recording system + microphone", "recording")
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
            messagebox.showerror("Error during recording", "\n".join(errors))
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
        # We stop the timer and disable the button immediately. The processing
        # (alignment + mix + saving to disk) is heavy and can take a while on
        # long recordings, so we do it on a separate thread to avoid freezing
        # (or crashing) the interface.
        self.recording = False
        self.visualizer.recording = False
        self.btn_stop.config(state="disabled")
        self._set_status("Processing and aligning tracks...", "busy")
        record_dir = self.record_dir.get().strip() or RECORD_DIR_DEFAULT
        transcript_dir = self.transcript_dir.get().strip() or record_dir
        prefix = getattr(self, "_meeting_prefix", DEFAULT_TRANSCRIPT_PREFIX)
        recorder = self.recorder
        self.recorder = None
        threading.Thread(
            target=self._stop_worker,
            args=(recorder, record_dir, transcript_dir, prefix), daemon=True).start()

    def _stop_worker(self, recorder, record_dir, transcript_dir, prefix):
        try:
            mixed, rate, sys_only, mic_only = recorder.stop_and_mix()
            errors = recorder.get_errors()
            recorder.close()

            os.makedirs(record_dir, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            # Only the final mix is saved in the recordings folder. The separate
            # tracks (mic/system) go to a temporary folder, used for the
            # "You / Them" mode and cleaned up when the app closes.
            temp_dir = self._ensure_temp_dir()
            self._clean_temp_tracks()
            paths = {
                "mixed": os.path.join(record_dir, f"meeting_{stamp}.wav"),
                "sys": os.path.join(temp_dir, f"meeting_{stamp}_system.wav"),
                "mic": os.path.join(temp_dir, f"meeting_{stamp}_mic.wav"),
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

    def _ensure_temp_dir(self):
        if not self._temp_dir or not os.path.isdir(self._temp_dir):
            self._temp_dir = tempfile.mkdtemp(prefix="maxrecorder_")
        return self._temp_dir

    def _clean_temp_tracks(self):
        """Deletes the temporary tracks (mic/system) from the previous recording."""
        if not self._temp_dir or not os.path.isdir(self._temp_dir):
            return
        for name in os.listdir(self._temp_dir):
            try:
                os.remove(os.path.join(self._temp_dir, name))
            except OSError:
                pass

    def _on_stop_done(self, paths, errors):
        self.btn_start.config(state="normal")
        self.btn_transcribe.config(state="normal")
        self.last_paths = paths
        self._set_status(f"Saved: {os.path.basename(paths['mixed'])}", "ready")
        if errors:
            messagebox.showwarning("Warnings during recording", "\n".join(errors))
        if self._quit_after_save:
            self._quit_after_save = False
            self._quit_app()
            return
        if self.auto_transcribe_var.get() and WHISPER_AVAILABLE:
            self._transcribe()

    def _on_stop_error(self, error):
        self.btn_start.config(state="normal")
        self.btn_stop.config(state="disabled")
        self._set_status("Error processing the recording", "idle")
        messagebox.showerror("Error stopping the recording", str(error))
        if self._quit_after_save:
            # Saving failed; we ask whether to close anyway.
            self._quit_after_save = False
            if messagebox.askyesno("Close", "The recording could not be saved.\nClose anyway?"):
                self._quit_app()

    # ---------------- Meeting detection ----------------

    def _toggle_auto_detect(self):
        if self.auto_detect_var.get():
            if not (PSUTIL_AVAILABLE and WIN32_AVAILABLE):
                # Detection is always on by design; if dependencies are missing
                # we only reflect it in the status (the warning is already
                # visible in the settings window) instead of interrupting
                # startup with a dialog.
                self.auto_detect_var.set(False)
                self._set_status("Detection unavailable (missing dependencies)", "idle")
                return
            keywords = [k for k in self.keywords_var.get().split(",")]
            self.meeting_watcher = MeetingWatcher(
                on_meeting_start=self._on_meeting_detected,
                on_meeting_end=None,
                keywords=keywords,
                poll_interval=self.poll_interval_var.get(),
            )
            self.meeting_watcher.start()
            self._set_status("Watching Teams...", "watching")
        else:
            if self.meeting_watcher:
                self.meeting_watcher.stop()
                self.meeting_watcher = None
            self._set_status("Detection off", "ready")

    def _on_meeting_detected(self):
        # Called from the watcher thread: we must hop to the Tkinter thread.
        self.after(0, self._show_meeting_popup)

    def _show_meeting_popup(self):
        if self.recording:
            return  # already recording, don't disturb
        MeetingPopup(self, on_accept=self._start_recording)

    def _test_popup(self):
        self._show_meeting_popup()

    # ---------------- Settings ----------------

    def _open_settings(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.lift()
            self.settings_window.focus_set()
            return
        self.settings_window = SettingsWindow(self)

    def _apply_settings(self):
        """Persists the settings and applies them to the running watcher."""
        try:
            poll = min(max(int(self.poll_interval_var.get()), 2), 30)
        except (tk.TclError, ValueError):
            poll = 4
        self.poll_interval_var.set(poll)
        save_config({
            "record_dir": self.record_dir.get().strip(),
            "transcript_dir": self.transcript_dir.get().strip(),
            "keywords": self.keywords_var.get(),
            "poll_interval": poll,
            "theme": self.theme_name,
        })
        if self.meeting_watcher:
            self.meeting_watcher.update_keywords(self.keywords_var.get().split(","))
            self.meeting_watcher.poll_interval = poll

    # ---------------- Theme ----------------

    def _apply_theme(self, name):
        """Switches the color theme and rebuilds the UI in place, preserving
        the current state (transcript text, mic list, recording status)."""
        if name == self.theme_name:
            return
        set_theme(name)
        self.theme_name = P.NAME

        # Preserve state that lives in widgets.
        transcript = self.txt_transcript.get("1.0", "end-1c")
        mic_values = list(self.mic_combo["values"])
        mic_index = self.mic_combo.current()
        model = self.whisper_model_combo.get()
        lang = self.lang_entry.get()
        tr_status = self.lbl_tr_status.cget("text")

        # Tear down and rebuild every widget with the new palette. Toplevels
        # (settings window, popups) are managed by their own code.
        for child in list(self.winfo_children()):
            if isinstance(child, tk.Toplevel):
                continue
            child.destroy()
        self.configure(bg=P.BG)
        self._setup_style()
        self._build_ui()

        # Restore state.
        self.mic_combo["values"] = mic_values
        if 0 <= mic_index < len(mic_values):
            self.mic_combo.current(mic_index)
        self.whisper_model_combo.set(model)
        self.lang_entry.delete(0, tk.END)
        self.lang_entry.insert(0, lang)
        self.lbl_tr_status.config(text=tr_status)
        if transcript:
            self.txt_transcript.insert("1.0", transcript)
        if self.recording:
            self.visualizer.recording = True
            self.btn_start.config(state="disabled")
            self.btn_stop.config(state="normal")
            self._set_status("Recording system + microphone", "recording")
        else:
            if self.last_paths:
                self.btn_transcribe.config(state="normal")
            if self.meeting_watcher and self.meeting_watcher.is_alive():
                self._set_status("Watching Teams...", "watching")

        self._apply_settings()

    # ---------------- Autostart ----------------

    def _toggle_autostart(self):
        if not WINREG_AVAILABLE:
            messagebox.showerror("Not available", "Autostart is only available on Windows.")
            self.autostart_var.set(False)
            return
        try:
            if self.autostart_var.get():
                enable_autostart()
                self._set_status("Autostart enabled", "ready")
            else:
                disable_autostart()
                self._set_status("Autostart disabled", "ready")
        except Exception as e:
            messagebox.showerror("Error", f"Could not change autostart:\n{e}")
            self.autostart_var.set(is_autostart_enabled())

    # ---------------- System tray ----------------

    def _setup_tray(self):
        image = self._make_tray_image()
        menu = pystray.Menu(
            pystray.MenuItem("Open", self._tray_open, default=True),
            pystray.MenuItem("Start recording", lambda: self.after(0, self._start_recording)),
            pystray.MenuItem("Stop recording", lambda: self.after(0, self._stop_recording)),
            pystray.MenuItem("Quit", self._tray_quit),
        )
        self.tray_icon = pystray.Icon("max_recorder", image, "Max Recorder", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _make_tray_image(self):
        # Same design as the window icon (maxrecorder/ui/assets/icon.ico).
        try:
            return Image.open(ICON_PATH)
        except (FileNotFoundError, OSError):
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
            messagebox.showerror("Missing dependency", "Install: pip install pystray pillow")
            return
        self.withdraw()

    def _on_close(self):
        if TRAY_AVAILABLE:
            if messagebox.askyesno(
                    "Minimize",
                    "Minimize to the system tray and keep watching for meetings?\n"
                    "(No = close the application completely)"):
                self.withdraw()
                return
        self._quit_app()

    def _quit_app(self):
        # Warn if a recording is in progress: avoids losing it on close.
        if self.recording:
            resp = messagebox.askyesnocancel(
                "Recording in progress",
                "A recording is in progress.\n\n"
                "- Yes: stop and save it before quitting.\n"
                "- No: discard it and quit (it is lost).\n"
                "- Cancel: don't close.",
                icon="warning")
            if resp is None:
                return  # Cancel: don't close
            if resp:
                # Save and quit: we stop (saves in the background) and close
                # when it finishes, from _on_stop_done / _on_stop_error.
                self._quit_after_save = True
                self._set_status("Saving before quitting...", "busy")
                self._stop_recording()
                return
            # No: discard the in-progress recording without saving.
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
        # Delete the temporary mic/system tracks (they are not kept on disk).
        if self._temp_dir:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
        self.destroy()

    # ---------------- Transcription ----------------

    def _transcribe(self):
        """Transcribes the last recording. If 'You / Them' is enabled and the
        separate tracks exist, transcribes each one and interleaves the
        segments with a speaker label."""
        if not self._check_whisper():
            return
        if not self.last_paths or not os.path.exists(self.last_paths["mixed"]):
            messagebox.showwarning("Notice", "Record and stop a recording first.")
            return

        jobs = None
        if self.speakers_var.get():
            sys_p, mic_p = self.last_paths.get("sys"), self.last_paths.get("mic")
            if sys_p and mic_p and os.path.exists(sys_p) and os.path.exists(mic_p):
                jobs = [(LABEL_THEM, sys_p), (LABEL_ME, mic_p)]
        if jobs is None:
            jobs = [(None, self.last_paths["mixed"])]

        # Path computed when the recording stopped (meeting_/weekly_YYYY-MM-DD.txt
        # in the transcripts folder); re-transcribing overwrites the same one.
        autosave = self.last_paths.get("txt")
        if not autosave:
            autosave = transcript_txt_path(
                self.transcript_dir.get().strip() or self.record_dir.get(),
                DEFAULT_TRANSCRIPT_PREFIX)
            self.last_paths["txt"] = autosave
        self._run_transcription(jobs, autosave_path=autosave)

    def _transcribe_file(self):
        """Transcribes any audio file chosen by the user."""
        if not self._check_whisper():
            return
        path = filedialog.askopenfilename(
            initialdir=self.record_dir.get(),
            filetypes=[("Audio", "*.wav *.mp3 *.m4a *.flac *.ogg *.opus"), ("All", "*.*")])
        if not path:
            return
        base = os.path.splitext(path)[0]
        self._run_transcription([(None, path)], autosave_path=base + "_transcription.txt")

    def _check_whisper(self):
        if not WHISPER_AVAILABLE:
            messagebox.showerror(
                "Missing dependency",
                "Install faster-whisper:\npip install faster-whisper")
            return False
        if self.transcribing:
            messagebox.showinfo("Transcription", "A transcription is already in progress.")
            return False
        return True

    def _run_transcription(self, jobs, autosave_path=None):
        self.transcribing = True
        self.btn_transcribe.config(state="disabled")
        self.txt_transcript.delete("1.0", tk.END)
        self.progress.set(None)  # indeterminate while the model loads
        self._set_status("Transcribing...", "busy")
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
            self.after(0, lambda: messagebox.showerror("Transcription error", str(e)))
            self.after(0, lambda: self._set_status("Error transcribing", "idle"))
            self.after(0, self.progress.hide)
        finally:
            self.transcribing = False
            self.after(0, lambda: self.btn_transcribe.config(
                state="normal" if self.last_paths else "disabled"))

    def _append_segment(self, start, label, text):
        """Streaming: appends a segment to the panel as it is transcribed."""
        if self.timestamps_var.get():
            self.txt_transcript.insert(tk.END, f"[{format_ts(start)}] ", "ts")
        if label:
            tag = "me" if label == LABEL_ME else "them"
            self.txt_transcript.insert(tk.END, f"{label}: ", tag)
        self.txt_transcript.insert(tk.END, text + "\n")
        self.txt_transcript.see(tk.END)

    def _on_transcribe_done(self, segs, multi, saved_path):
        # With several tracks the segments arrived interleaved per track;
        # we rewrite the panel already ordered by time.
        if multi:
            self.txt_transcript.delete("1.0", tk.END)
            for start, label, text in segs:
                self._append_segment(start, label, text)
        self.progress.set(1.0)
        n = len(segs)
        msg = f"{n} segments"
        if saved_path:
            msg += f" · saved {os.path.basename(saved_path)}"
        self.lbl_tr_status.config(text=msg)
        self._set_status("Transcription complete", "ready")

    def _save_transcript(self):
        text = self.txt_transcript.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("Notice", "There is no transcript to save.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt", initialdir=self.transcript_dir.get() or self.record_dir.get(),
            initialfile="transcript.txt")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
