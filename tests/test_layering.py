"""Enforce the architectural layering by scanning import statements.

The rules the whole design rests on:

* ``data``     -- pure domain. No tkinter, no ibapi, no other quant_hub layer.
* ``services`` -- may use ``data``, ``storage``, and broker *interfaces*;
                  never GUI or ibapi.
* ``broker``   -- the only package allowed to import ibapi; never GUI/storage.
* ``storage``  -- sqlite3 + ``data`` only; never GUI, broker, or ibapi.
* ``visualization`` -- matplotlib only; no tkinter (embedding happens in gui).

Source files are scanned as text (rather than imported) so the test needs no
GUI display and cannot be fooled by import-time side effects.
"""

import re
from pathlib import Path

import quant_hub

PACKAGE_ROOT = Path(quant_hub.__file__).parent

# package (relative to quant_hub) -> forbidden import roots
RULES = {
    "data": ["tkinter", "ibapi", "quant_hub.gui", "quant_hub.broker",
             "quant_hub.services", "quant_hub.visualization", "quant_hub.storage"],
    "models": ["tkinter", "ibapi", "quant_hub.gui", "quant_hub.broker",
               "quant_hub.services", "quant_hub.visualization", "quant_hub.storage"],

    "services": ["tkinter", "ibapi", "quant_hub.gui", "quant_hub.visualization"],
    "broker": ["tkinter", "quant_hub.gui", "quant_hub.visualization",
               "quant_hub.storage"],
    "storage": ["tkinter", "ibapi", "quant_hub.gui", "quant_hub.broker",
                "quant_hub.services", "quant_hub.visualization"],
    "visualization": ["tkinter", "ibapi", "quant_hub.gui", "quant_hub.broker",
                      "quant_hub.services", "quant_hub.storage"],
}

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
