"""Quant Hub: a modular quantitative research and trading workstation.

Layering (imports may only point downward):

    gui/            Tkinter presentation. No math, no broker calls.
    services/       Application facade. Orchestrates data sources.
    data/           Pure domain code (NumPy/pandas only). Usable from
                    notebooks, scripts, and tests without the GUI.
    broker/         Infrastructure. The only package that touches ibapi.

Cross-cutting: ``events`` (thread-safe bus), ``config`` (static settings).
``tests/test_layering.py`` enforces the import rules.
"""

__version__ = "0.1.0"
