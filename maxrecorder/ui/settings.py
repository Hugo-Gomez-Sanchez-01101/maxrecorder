"""Settings window: collapsible sections (appearance, folders, AI summary,
background) inside a scrollable body. Saved to config.json on close."""

import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from ..i18n import tr
from ..autostart import WINREG_AVAILABLE
from ..detection import PSUTIL_AVAILABLE, WIN32_AVAILABLE
from ..summary import (REQUESTS_AVAILABLE, extract_notion_database_id,
                       test_nvidia_key, test_notion_key, test_database)
from .theme import (P, TechButton, make_collapsible_section,
                    dark_entry, dark_check, dark_label)

try:
    import pystray  # noqa: F401
    from PIL import Image  # noqa: F401
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False


HELP_NOTION_KEY = (
    "How to get your Notion API key:\n\n"
    "1. Go to https://app.notion.com/developers/connections\n"
    "2. Create a new connection (integration): this gives you an access "
    "token — that is the API key.\n"
    "3. Give the connection access to the workspace that contains your "
    "calendar database.\n"
    "4. In Notion, open the page that contains the calendar, click the ... "
    "menu (top right) > Connections, and add your integration so it can "
    "access that page."
)

HELP_NVIDIA_KEY = (
    "How to get your Mistral API key (free, via NVIDIA):\n\n"
    "1. Go to https://build.nvidia.com/mistralai/mistral-medium-3.5-128b\n"
    "2. Sign in (create a free account if needed).\n"
    "3. Generate an API key and copy it here."
)

HELP_NOTION_DB = (
    "How to get your Notion calendar link:\n\n"
    "1. In Notion, next to the calendar database name, click the ... menu.\n"
    "2. Choose 'Copy link to view'.\n"
    "3. Paste the full link here (e.g. https://www.notion.so/3726c2...?v=...).\n\n"
    "The app extracts the database ID from the link automatically when you "
    "save. You can also paste the 32-character ID directly."
)


class SettingsWindow(tk.Toplevel):

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title(tr("Settings — Max Recorder"))
        self.configure(bg=P.BG)
        self.resizable(False, False)
        self.transient(app)
        self.geometry(f"+{app.winfo_rootx() + 90}+{app.winfo_rooty() + 40}")
        self.protocol("WM_DELETE_WINDOW", self._save_close)

        tk.Label(self, text=tr("SETTINGS"), bg=P.BG, fg=P.ACCENT,
                 font=("Consolas", 12, "bold"), anchor="w").pack(
            fill="x", padx=12, pady=(10, 0))

        # ---- Scrollable body (sections are collapsed by default) ----
        container = tk.Frame(self, bg=P.BG)
        container.pack(fill="both", expand=True)
        self._canvas = tk.Canvas(container, bg=P.BG, highlightthickness=0,
                                 width=680, height=420)
        scrollbar = ttk.Scrollbar(container, orient="vertical",
                                  command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)
        body = tk.Frame(self._canvas, bg=P.BG)
        body_window = self._canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>",
                  lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>",
                          lambda e: self._canvas.itemconfigure(body_window, width=e.width))
        # The toplevel is in every child's bindtags, so this catches the wheel
        # anywhere inside this window without leaking to the main window.
        self.bind("<MouseWheel>",
                  lambda e: self._canvas.yview_scroll(int(-e.delta / 120), "units"))

        # ---- Appearance ----
        _, appearance = make_collapsible_section(body, tr("Appearance"))
        row_theme = tk.Frame(appearance, bg=P.PANEL)
        row_theme.pack(fill="x", pady=2)
        dark_label(row_theme, text=tr("Theme:")).pack(side="left", padx=(2, 8))
        # The active theme gets the 'primary' (accent) button.
        TechButton(row_theme, text="DARK",
                   kind="primary" if app.theme_name == "dark" else "ghost",
                   command=lambda: self._set_theme("dark"), width=10).pack(side="left", padx=(0, 6))
        TechButton(row_theme, text="TE FIELD",
                   kind="primary" if app.theme_name == "te" else "ghost",
                   command=lambda: self._set_theme("te"), width=10).pack(side="left", padx=(0, 6))
        TechButton(row_theme, text="POLAROID",
                   kind="primary" if app.theme_name == "polaroid" else "ghost",
                   command=lambda: self._set_theme("polaroid"), width=10).pack(side="left")
        dark_label(row_theme, dim=True,
                   text=tr("(TE FIELD: light Teenage Engineering inspired look)")).pack(
            side="left", padx=10)

        # ---- Folders ----
        _, folders = make_collapsible_section(body, tr("Folders"))
        row_rec = tk.Frame(folders, bg=P.PANEL)
        row_rec.pack(fill="x", pady=2)
        dark_label(row_rec, text=tr("Recordings:")).pack(side="left", padx=(2, 4))
        dark_entry(row_rec, textvariable=app.record_dir, width=45).pack(
            side="left", padx=4, fill="x", expand=True, ipady=3)
        TechButton(row_rec, text=tr("CHOOSE..."), command=self._choose_record_dir).pack(side="left", padx=4)

        row_tr = tk.Frame(folders, bg=P.PANEL)
        row_tr.pack(fill="x", pady=2)
        dark_label(row_tr, text=tr("Transcripts:")).pack(side="left", padx=(2, 4))
        dark_entry(row_tr, textvariable=app.transcript_dir, width=45).pack(
            side="left", padx=4, fill="x", expand=True, ipady=3)
        TechButton(row_tr, text=tr("CHOOSE..."), command=self._choose_transcript_dir).pack(side="left", padx=4)

        # ---- AI summary ----
        _, ai = make_collapsible_section(body, tr("AI summary"))
        dark_label(ai, dim=True,
                   text=tr("The AI SUMMARY button shows a Markdown summary you can "
                           "copy. It only needs the Mistral key.")).pack(fill="x", pady=(0, 2))
        self._credential_row(ai, tr("Mistral API key (NVIDIA):"), app.nvidia_key_var,
                             tr("Mistral API key"), tr(HELP_NVIDIA_KEY), secret=True)

        row_en = tk.Frame(ai, bg=P.PANEL)
        row_en.pack(fill="x", pady=(8, 2))
        dark_check(row_en,
                   text=tr("Also publish the summary to a Notion calendar (optional)"),
                   variable=app.notion_enabled_var).pack(side="left")

        self._credential_row(ai, tr("Notion API key:"), app.notion_key_var,
                             tr("Notion API key"), tr(HELP_NOTION_KEY), secret=True)
        self._credential_row(ai, tr("Notion calendar link or ID:"), app.notion_db_var,
                             tr("Notion calendar"), tr(HELP_NOTION_DB), secret=False)

        row_test = tk.Frame(ai, bg=P.PANEL)
        row_test.pack(fill="x", pady=(4, 2))
        self._btn_test = TechButton(row_test, text=tr("TEST CONNECTIONS"),
                                    command=self._test_ai)
        self._btn_test.pack(side="left", padx=2)
        self.lbl_test = dark_label(row_test, dim=True, text="")
        self.lbl_test.pack(side="left", padx=8)

        if not REQUESTS_AVAILABLE:
            tk.Label(ai, text=tr("Missing dependency for this feature: pip install requests"),
                     bg=P.PANEL, fg=P.AMBER, font=P.FONT_SM, anchor="w").pack(fill="x")

        # ---- Background ----
        _, bg_sec = make_collapsible_section(body, tr("Background · Meeting detection"))
        row1 = tk.Frame(bg_sec, bg=P.PANEL)
        row1.pack(fill="x", pady=2)
        dark_check(row1, text=tr("Automatically detect meetings and notify (always on)"),
                   variable=app.auto_detect_var, state="disabled",
                   disabledforeground=P.TEXT).pack(side="left")
        dark_label(row1, text=tr("Poll (s):")).pack(side="left", padx=(16, 4))
        tk.Spinbox(row1, from_=2, to=30, width=4, textvariable=app.poll_interval_var,
                   bg=P.FIELD, fg=P.TEXT, buttonbackground=P.PANEL2,
                   insertbackground=P.ACCENT, relief="flat",
                   highlightthickness=1, highlightbackground=P.BORDER).pack(side="left")

        row2 = tk.Frame(bg_sec, bg=P.PANEL)
        row2.pack(fill="x", pady=2)
        dark_label(row2, text=tr("Keywords (title fallback):")).pack(side="left", padx=(2, 4))
        dark_entry(row2, textvariable=app.keywords_var).pack(
            side="left", padx=4, fill="x", expand=True, ipady=3)

        row3 = tk.Frame(bg_sec, bg=P.PANEL)
        row3.pack(fill="x", pady=2)
        dark_check(row3,
                   text=tr("Start automatically at Windows login (in the background)"),
                   variable=app.autostart_var, command=app._toggle_autostart).pack(side="left")
        if not WINREG_AVAILABLE:
            lbl_no_reg = dark_label(row3, text=tr("(not available on this platform)"))
            lbl_no_reg.config(fg=P.RED)
            lbl_no_reg.pack(side="left", padx=6)

        row4 = tk.Frame(bg_sec, bg=P.PANEL)
        row4.pack(fill="x", pady=(4, 2))
        TechButton(row4, text=tr("TEST NOTIFICATION"), command=app._test_popup).pack(side="left", padx=2)

        if not (PSUTIL_AVAILABLE and WIN32_AVAILABLE):
            tk.Label(bg_sec, text=tr("Missing dependencies for detection: pip install psutil pywin32"),
                     bg=P.PANEL, fg=P.AMBER, font=P.FONT_SM, anchor="w").pack(fill="x")
        if not TRAY_AVAILABLE:
            tk.Label(bg_sec, text=tr("For background mode install: pip install pystray pillow"),
                     bg=P.PANEL, fg=P.AMBER, font=P.FONT_SM, anchor="w").pack(fill="x")

        # ---- Close ----
        btns = tk.Frame(self, bg=P.BG)
        btns.pack(fill="x", padx=12, pady=10)
        TechButton(btns, kind="primary", text=tr("SAVE AND CLOSE"),
                   command=self._save_close).pack(side="right")

    def _credential_row(self, parent, label, variable, help_title, help_text, secret):
        """Row with label + entry + '?' help button. Secret entries are masked."""
        row = tk.Frame(parent, bg=P.PANEL)
        row.pack(fill="x", pady=2)
        dark_label(row, text=label, width=24, anchor="w").pack(side="left", padx=(2, 4))
        kw = {"show": "•"} if secret else {}
        dark_entry(row, textvariable=variable, **kw).pack(
            side="left", padx=4, fill="x", expand=True, ipady=3)
        TechButton(row, text="?", width=2,
                   command=lambda: messagebox.showinfo(help_title, help_text, parent=self)
                   ).pack(side="left", padx=4)

    def _test_ai(self):
        """Checks the three AI-summary credentials (Mistral key, Notion key and
        calendar access) and reports each one."""
        nvidia_key = self.app.nvidia_key_var.get().strip()
        notion_key = self.app.notion_key_var.get().strip()
        db_id = extract_notion_database_id(self.app.notion_db_var.get())
        notion_on = self.app.notion_enabled_var.get()
        self._btn_test.config(state="disabled")
        self.lbl_test.config(text=tr("Testing..."))

        def worker():
            parts = []
            try:
                test_nvidia_key(nvidia_key)
                parts.append("Mistral ✓")
            except Exception as e:
                parts.append(f"Mistral ✗ ({str(e)[:40]})")
            if notion_on:
                try:
                    test_notion_key(notion_key)
                    parts.append("Notion key ✓")
                except Exception as e:
                    parts.append(f"Notion key ✗ ({str(e)[:40]})")
                try:
                    test_database(notion_key, db_id)
                    parts.append("Calendar ✓")
                except Exception as e:
                    parts.append(f"Calendar ✗ ({str(e)[:60]})")
            else:
                parts.append(tr("Notion disabled"))
            result = "  ·  ".join(parts)

            def show():
                try:
                    if self.winfo_exists():
                        self.lbl_test.config(text=result)
                        self._btn_test.config(state="normal")
                except tk.TclError:
                    pass
            self.app.after(0, show)

        threading.Thread(target=worker, daemon=True).start()

    def _set_theme(self, name):
        """Applies the theme to the main window and reopens this window so it
        picks up the new palette too."""
        if name == self.app.theme_name:
            return
        self.destroy()
        self.app.settings_window = None
        self.app._apply_theme(name)
        self.app._open_settings()

    def _choose_record_dir(self):
        d = filedialog.askdirectory(
            initialdir=self.app.record_dir.get(), parent=self)
        if d:
            self.app.record_dir.set(d)

    def _choose_transcript_dir(self):
        d = filedialog.askdirectory(
            initialdir=self.app.transcript_dir.get() or self.app.record_dir.get(),
            parent=self)
        if d:
            self.app.transcript_dir.set(d)

    def _save_close(self):
        self.app._apply_settings()
        self.destroy()
