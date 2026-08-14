"""Scrolling application log fed by ``LogEvent``s from the event bus.

Because the bus's ``dispatch_pending`` runs on the GUI thread, the handler
here may touch widgets directly.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from quant_hub.events import EventBus, LogEvent
from quant_hub.gui.theme import FONT_MONO_SM, PALETTE

_MAX_LINES = 500

_LEVEL_TAGS = {
    "DEBUG": ("debug", PALETTE.muted),
    "INFO": ("info", PALETTE.text_secondary),
    "WARNING": ("warning", PALETTE.warning),
    "ERROR": ("error", PALETTE.critical),
    "CRITICAL": ("error", PALETTE.critical),
}


class LogPanel(ttk.Frame):
    """Collapsible: the header row (with the latest message) is always
    visible; the scrolling text toggles via the ▾/▸ button. No persistence —
    the panel simply starts expanded each session."""

    def __init__(self, parent: tk.Misc, bus: EventBus) -> None:
        super().__init__(parent, style="Surface.TFrame")

        header = ttk.Frame(self, style="Surface.TFrame")
        header.pack(fill="x", padx=10, pady=(6, 2))
        self._toggle = ttk.Button(
            header, text="▾ LOG", style="Nav.TButton", width=7, command=self._on_toggle
        )
        self._toggle.pack(side="left")
        self._latest = ttk.Label(header, text="", style="Muted.TLabel")
        self._latest.pack(side="left", padx=(12, 0))
        self._expanded = True

        self._text = tk.Text(
            self,
            height=5,
            wrap="none",
            state="disabled",
            background=PALETTE.surface,
            foreground=PALETTE.text_secondary,
            insertbackground=PALETTE.text,
            selectbackground=PALETTE.select_bg,
            font=FONT_MONO_SM,
            borderwidth=0,
            highlightthickness=0,
            padx=10,
            pady=2,
        )
        self._scrollbar = ttk.Scrollbar(self, command=self._text.yview)
        self._text.configure(yscrollcommand=self._scrollbar.set)
        self._scrollbar.pack(side="right", fill="y")
        self._text.pack(fill="both", expand=True)

        self._text.tag_configure("timestamp", foreground=PALETTE.muted)
        for tag, color in set(_LEVEL_TAGS.values()):
            self._text.tag_configure(tag, foreground=color)

        bus.subscribe(LogEvent, self._on_log_event)

    def _on_toggle(self) -> None:
        self._expanded = not self._expanded
        if self._expanded:
            self._toggle.configure(text="▾ LOG")
            self._scrollbar.pack(side="right", fill="y")
            self._text.pack(fill="both", expand=True)
        else:
            self._toggle.configure(text="▸ LOG")
            self._text.pack_forget()
            self._scrollbar.pack_forget()

    def _on_log_event(self, event: LogEvent) -> None:
        self._latest.configure(text=f"{event.timestamp:%H:%M:%S}  {event.message[:120]}")
        tag, _ = _LEVEL_TAGS.get(event.level, _LEVEL_TAGS["INFO"])
        self._text.configure(state="normal")
        self._text.insert("end", f"{event.timestamp:%H:%M:%S}  ", ("timestamp",))
        self._text.insert("end", f"{event.level:<8}", (tag,))
        self._text.insert("end", f"{event.message}\n", (tag,))
        # Cap the buffer so a long session cannot grow memory unboundedly.
        excess = int(self._text.index("end-1c").split(".")[0]) - _MAX_LINES
        if excess > 0:
            self._text.delete("1.0", f"{excess + 1}.0")
        self._text.configure(state="disabled")
        self._text.see("end")
