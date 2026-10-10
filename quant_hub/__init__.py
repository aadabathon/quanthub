"""Quant Hub: a modular quantitative research and trading workstation.

Layering (imports may only point downward):

    gui/            Qt (PySide6 + pyqtgraph) presentation. No math, no
                    broker calls.
    services/       Application facade + market-data cache/sync.
    models/         Pure quantitative models (consume data/ only).
    data/           Pure domain code (NumPy/pandas only). Usable from
                    notebooks, scripts, and tests without the GUI.
    broker/         Infrastructure. The only package that touches ibapi.
    storage/        SQLite persistence (stdlib sqlite3).
    visualization/  Matplotlib figures for scripts and notebooks.

Cross-cutting: ``events`` (thread-safe bus), ``tasks`` (worker thread),
``config`` (static settings); ``core`` + ``app`` wire everything.
``tests/test_layering.py`` enforces the import rules.
"""

__version__ = "0.5.0"
