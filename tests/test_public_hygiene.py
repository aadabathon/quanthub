"""What this public repository must never carry (D113).

Quant Hub is public, and its maintainer's package of personal pages is
private (D87). The private package's name, its plan files, its rule file, the
trading chat's brief, and talk of "the" + "owner" belong there. Each slipped
into public code at least once, so the suite refuses them. The patterns are
built from pieces, so this file doesn't match itself.
"""

import re
from pathlib import Path

import quant_hub

REPO = Path(quant_hub.__file__).resolve().parent.parent
PRIVATE = re.compile("|".join([r"qh_" + "personal", r"REGIME\.md", r"\b[A-Z]{2,}_" + r"PLAN\b",
                               "TRADING_" + "CHAT", r"\bthe " + r"owner\b"]), re.IGNORECASE)
TEXT = {".py", ".md", ".toml", ".yml", ".yaml", ".json", ".txt", ".cfg", ".ini"}
SKIP = {".git", ".claude", "build", "dist", "__pycache__", ".pytest_cache", ".ruff_cache"}


def test_the_patterns_catch_each_mark():
    # Built from pieces, as above; a pattern that matches nothing passes
    # everything, so each is checked against a line it must catch.
    for line in ("import qh_" + "personal", "see REGIME" + ".md", "per DESK" + "_PLAN D5",
                 "the TRADING" + "_CHAT brief", "as The " + "Owner said"):
        assert PRIVATE.search(line), line
    assert not PRIVATE.search("the owners' guide, PLANNED, REGIME_md")


def test_no_private_marks_in_the_public_tree():
    here = Path(__file__).resolve()
    found = []
    for path in sorted(REPO.rglob("*")):
        parts = path.relative_to(REPO).parts
        if (path.suffix not in TEXT or path.resolve() == here or not path.is_file()
                or any(p in SKIP or p.endswith(".egg-info") for p in parts)):
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(),
                                 1):
            if PRIVATE.search(line):
                found.append(f"{path.relative_to(REPO)}:{n}: {line.strip()[:90]}")
    assert not found, "private marks in the public tree:\n" + "\n".join(found)
