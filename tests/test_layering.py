"""Enforce the architectural layering by scanning import statements.

The rules the whole design rests on:

* ``data``     -- pure domain. No ibapi, no other quant_hub layer.
* ``models``   -- pure quantitative models; may use ``data`` only.
* ``services`` -- may use ``data``, ``storage``, and broker *interfaces*;
                  never GUI or ibapi.
* ``broker``   -- the only package allowed to import ibapi; never GUI/storage.
* ``storage``  -- sqlite3 + ``data`` only; never GUI, broker, or ibapi.
* ``visualization`` -- matplotlib figures for scripts/notebooks; never GUI.
* ``gui``      -- the Qt front end (D31): never ibapi, the concrete broker, or
                  the bar store (it reads market data through services).
* Qt libraries (PySide6, pyqtgraph, qtawesome, qtpy) are imported only by
  ``gui`` and the composition root ``app.py``; tkinter by nothing (retired).

Source files are scanned as text (rather than imported) so the test needs no
GUI display and cannot be fooled by import-time side effects.
"""

import re
from pathlib import Path

import quant_hub

PACKAGE_ROOT = Path(quant_hub.__file__).parent

# package (relative to quant_hub) -> forbidden import roots
RULES = {
    "data": ["ibapi", "quant_hub.gui", "quant_hub.broker", "quant_hub.services",
             "quant_hub.visualization", "quant_hub.storage"],
    "models": ["ibapi", "quant_hub.gui", "quant_hub.broker", "quant_hub.services",
               "quant_hub.visualization", "quant_hub.storage"],
    "services": ["ibapi", "quant_hub.gui", "quant_hub.visualization"],
    "broker": ["quant_hub.gui", "quant_hub.visualization", "quant_hub.storage"],
    "storage": ["ibapi", "quant_hub.gui", "quant_hub.broker", "quant_hub.services",
                "quant_hub.visualization"],
    "visualization": ["ibapi", "quant_hub.gui", "quant_hub.broker", "quant_hub.services",
                      "quant_hub.storage"],
    "gui": ["ibapi", "quant_hub.broker.ibkr", "quant_hub.broker.market_source",
            "quant_hub.storage.bar_store"],
}

QT_LIBRARIES = ("PySide6", "pyqtgraph", "qtawesome", "qtpy")

_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([A-Za-z_][\w.]*)\s+import|import\s+([A-Za-z_][\w.]*))",
    re.MULTILINE,
)


def imports_of(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    found = set()
    for match in _IMPORT_RE.finditer(text):
        found.add(match.group(1) or match.group(2))
    return found


def test_layering_rules():
    violations = []
    for package, forbidden_roots in RULES.items():
        for path in (PACKAGE_ROOT / package).rglob("*.py"):
            for module in imports_of(path):
                for root in forbidden_roots:
                    if module == root or module.startswith(root + "."):
                        violations.append(
                            f"{path.relative_to(PACKAGE_ROOT)} imports {module}"
                        )
    assert not violations, "layering violations:\n" + "\n".join(violations)


def test_only_broker_imports_ibapi():
    offenders = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        if "broker" in path.relative_to(PACKAGE_ROOT).parts:
            continue
        if any(m == "ibapi" or m.startswith("ibapi.") for m in imports_of(path)):
            offenders.append(str(path.relative_to(PACKAGE_ROOT)))
    assert not offenders, f"ibapi imported outside broker/: {offenders}"


def test_only_gui_imports_qt():
    offenders = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        relative = path.relative_to(PACKAGE_ROOT)
        if relative.parts[0] == "gui" or relative.name == "app.py":
            continue
        for module in imports_of(path):
            if module.split(".")[0] in QT_LIBRARIES:
                offenders.append(f"{relative} imports {module}")
    assert not offenders, "Qt imported outside gui/:\n" + "\n".join(offenders)


def test_nothing_imports_tkinter():
    """The Tk front end was retired (D31); a stray import would pull it back."""
    offenders = [str(path.relative_to(PACKAGE_ROOT)) for path in PACKAGE_ROOT.rglob("*.py")
                 if any(m.split(".")[0] == "tkinter" for m in imports_of(path))]
    assert not offenders, f"tkinter imported: {offenders}"
