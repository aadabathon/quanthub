"""The Qt (PySide6 + pyqtgraph) front end — the replacement for ``gui/`` (D31).

``qtawesome`` reaches Qt through ``qtpy``, which picks a binding at import
time; pin it to PySide6 here, before any submodule can import qtawesome.
"""

import os

os.environ.setdefault("QT_API", "pyside6")
