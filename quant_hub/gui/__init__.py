"""The GUI: a PySide6 (Qt) front end with pyqtgraph charts (D31).

``qtawesome`` reaches Qt through ``qtpy``, which picks a binding at import
time; pin it to PySide6 here, before any submodule can import qtawesome.

The 3D surface embeds Chromium (Qt WebEngine), whose GPU process logs
harmless compositor errors to stderr; keep only its fatal messages.
"""

import os

os.environ.setdefault("QT_API", "pyside6")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--log-level=3")
