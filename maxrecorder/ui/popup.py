"""Detected-meeting notification popup, with a slide-in animation."""

import tkinter as tk

from ..i18n import tr
from .theme import P, StatusLED, TechButton


class MeetingPopup(tk.Toplevel):
    def __init__(self, master, on_accept, on_dismiss=None, timeout=30):
        super().__init__(master)
        self.on_accept = on_accept
        self.on_dismiss = on_dismiss
        self._closed = False

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.w, self.h = 350, 148
        sx, sy = self.winfo_screenwidth(), self.winfo_screenheight()
        self._final_x = sx - self.w - 24
        self._y = sy - self.h - 70
        # starts off-screen (right) and slides in
        self._x = float(sx)
        self.geometry(f"{self.w}x{self.h}+{int(self._x)}+{self._y}")
        self.configure(bg=P.PANEL, highlightthickness=1, highlightbackground=P.ACCENT)

        head = tk.Frame(self, bg=P.PANEL)
        head.pack(fill="x", padx=14, pady=(12, 2))
        self._led = StatusLED(head, bg=P.PANEL)
        self._led.set_state("recording")
        self._led.pack(side="left", padx=(0, 8))
        tk.Label(head, text=tr("TEAMS MEETING DETECTED"),
                 bg=P.PANEL, fg=P.TEXT, font=("Consolas", 10, "bold"),
                 anchor="w").pack(side="left")

        tk.Label(self, text=tr("Do you want to start recording now?"),
                 bg=P.PANEL, fg=P.DIM, font=P.FONT, anchor="w",
                 justify="left").pack(fill="x", padx=14, pady=(2, 10))

        btns = tk.Frame(self, bg=P.PANEL)
        btns.pack(padx=14, pady=4, fill="x")
        TechButton(btns, kind="danger", text=tr("●  RECORD"),
                   command=self._accept, width=11).pack(side="left", padx=(0, 8))
        TechButton(btns, kind="ghost", text=tr("DISMISS"),
                   command=self._dismiss, width=11).pack(side="left")

        self._slide_in()
        self.after(timeout * 1000, self._auto_dismiss)

    def _slide_in(self):
        if self._closed:
            return
        dist = self._x - self._final_x
        if dist <= 1:
            self._x = self._final_x
        else:
            self._x -= max(dist * 0.25, 2)
        self.geometry(f"{self.w}x{self.h}+{int(self._x)}+{self._y}")
        if self._x > self._final_x:
            self.after(15, self._slide_in)

    def _accept(self):
        self._close()
        self.on_accept()

    def _dismiss(self):
        self._close()
        if self.on_dismiss:
            self.on_dismiss()

    def _auto_dismiss(self):
        if not self._closed:
            self._dismiss()

    def _close(self):
        if not self._closed:
            self._closed = True
            self.destroy()
