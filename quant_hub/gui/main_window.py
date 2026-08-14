"""The application shell: header, navigation rail, view container, log panel.

Also owns the GUI pump -- a single ``root.after`` loop that drains the event
bus and the task-result queue on the GUI thread. That pump is the only place
background work re-enters Tkinter.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from tkinter import ttk

from quant_hub.broker.base import Broker
from quant_hub.events import EventBus
from quant_hub.gui.task_runner import TaskRunner
from quant_hub.gui.views.base import View
from quant_hub.gui.widgets.log_panel import LogPanel
from quant_hub.gui.widgets.status_bar import ConnectionBar

ViewFactory = Callable[[tk.Misc], View]
# Navigation is grouped into titled sections (None = untitled): core pages
# first, then the planned model modules behind a separator.
ViewSections = list[tuple[str | None, list[tuple[str, ViewFactory]]]]


class MainWindow(ttk.Frame):
    def __init__(
        self,
        root: tk.Tk,
        view_sections: ViewSections,
        broker: Broker,
        tasks: TaskRunner,
        bus: EventBus,
        pump_interval_ms: int,
    ) -> None:
        super().__init__(root, style="Page.TFrame")
        self._root = root
        self._bus = bus
        self._tasks = tasks
        self._pump_interval_ms = pump_interval_ms

        self._factories = {
            name: factory for _, specs in view_sections for name, factory in specs
        }
        self._views: dict[str, View] = {}       # created lazily
        self._nav_buttons: dict[str, ttk.Button] = {}
        self._active_name: str | None = None

        self.pack(fill="both", expand=True)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        # -- header ------------------------------------------------------
        header = ttk.Frame(self, style="Page.TFrame")
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="QUANT HUB", style="Title.TLabel").grid(
            row=0, column=0, sticky="w", padx=18, pady=10
        )
        ConnectionBar(header, broker, tasks, bus).grid(
            row=0, column=2, sticky="e", padx=18
        )

        # -- navigation rail ----------------------------------------------
        nav = ttk.Frame(self, style="Nav.TFrame", width=170)
        nav.grid(row=1, column=0, sticky="ns")
        nav.grid_propagate(False)
        for index, (section_title, specs) in enumerate(view_sections):
            if index > 0:
                ttk.Separator(nav).pack(fill="x", padx=12, pady=(10, 4))
            if section_title:
                ttk.Label(nav, text=section_title, style="NavSection.TLabel").pack(
                    fill="x", padx=16, pady=(2, 2)
                )
            for name, _factory in specs:
                button = ttk.Button(
                    nav,
                    text=name,
                    style="Nav.TButton",
                    command=lambda n=name: self.show_view(n),
                )
                button.pack(fill="x")
                self._nav_buttons[name] = button

        # -- view container -----------------------------------------------
        self._container = ttk.Frame(self, style="Surface.TFrame")
        self._container.grid(row=1, column=1, sticky="nsew", padx=(1, 0))
        self._container.columnconfigure(0, weight=1)
        self._container.rowconfigure(0, weight=1)

        # -- log panel ----------------------------------------------------
        log = LogPanel(self, bus)
        log.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(1, 0))

        first_view = next(iter(self._factories))
        self.show_view(first_view)
        self._pump()

    def show_view(self, name: str) -> None:
        """Raise (lazily creating) the named view and update the nav rail."""
        if name == self._active_name:
            return
        if name not in self._views:
            view = self._factories[name](self._container)
            view.grid(row=0, column=0, sticky="nsew")
            self._views[name] = view

        if self._active_name is not None:
            self._views[self._active_name].on_hide()
            self._nav_buttons[self._active_name].configure(style="Nav.TButton")

        self._active_name = name
        self._nav_buttons[name].configure(style="NavActive.TButton")
        view = self._views[name]
        view.tkraise()
        view.on_show()

    def _pump(self) -> None:
        """Drain thread-crossing queues on the GUI thread, forever."""
        self._bus.dispatch_pending()
        self._tasks.process_results()
        self._root.after(self._pump_interval_ms, self._pump)
