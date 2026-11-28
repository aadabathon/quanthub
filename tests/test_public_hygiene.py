"""What this public repository must never carry (D113).

Quant Hub is public, and its maintainer's package of personal pages is
private (D87). The private package's name, its plan files, its rule file, the
trading chat's brief, and talk of "the" + "owner" belong there. Each slipped
into public code at least once, so the suite refuses them. The patterns are
built from pieces, so this file doesn't match itself.

Only the files git would commit are read: tracked ones, and new ones it
doesn't ignore. An ignored file, such as a maintainer's local notes, never
reaches the public repository, so it can say what it likes.
"""

import re
import subprocess
from pathlib import Path

import pytest

import quant_hub

REPO = Path(quant_hub.__file__).resolve().parent.parent
PRIVATE = re.compile("|".join([r"qh_" + "personal", r"REGIME\.md", r"\b[A-Z]{2,}_" + r"PLAN\b",
                               "TRADING_" + "CHAT", r"\bthe " + r"owner\b"]), re.IGNORECASE)
TEXT = {".py", ".md", ".toml", ".yml", ".yaml", ".json", ".txt", ".cfg", ".ini"}
SKIP = {".git", ".claude", "build", "dist", "__pycache__", ".pytest_cache", ".ruff_cache"}


def committable(root: Path) -> list[Path]:
    """Every file under ``root`` that git would commit: tracked, or new and
    not ignored (by .gitignore, .git/info/exclude or the user's excludes).
    Outside a git checkout, such as an unpacked sdist, every file."""
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return [path for path in root.rglob("*") if path.is_file()]
    # -z: names unquoted, NUL-separated. A tracked file deleted on disk is
    # still listed, hence the is_file check.
    paths = (root / name for name in listed.decode("utf-8").split("\0") if name)
    return [path for path in paths if path.is_file()]


def test_the_scan_reads_what_git_would_commit_and_skips_what_it_ignores(tmp_path):
    try:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("needs git")
    (tmp_path / ".gitignore").write_text("notes.local.md\n", encoding="utf-8")
    (tmp_path / "notes.local.md").write_text("local\n", encoding="utf-8")
    (tmp_path / "new.md").write_text("new\n", encoding="utf-8")
    names = {path.name for path in committable(tmp_path)}
    assert "new.md" in names and ".gitignore" in names
    assert "notes.local.md" not in names


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
    for path in sorted(committable(REPO)):
        parts = path.relative_to(REPO).parts
        if (path.suffix not in TEXT or path.resolve() == here
                or any(p in SKIP or p.endswith(".egg-info") for p in parts)):
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(),
                                 1):
            if PRIVATE.search(line):
                found.append(f"{path.relative_to(REPO)}:{n}: {line.strip()[:90]}")
    assert not found, "private marks in the public tree:\n" + "\n".join(found)
