"""Read the literature canon from its JSON files (Lit-1).

The canon ships with the package in ``data/canon/`` (package data in
pyproject): ``timeline.json`` orders the strands and holds the eras, and
each strand's papers are in ``<strand id>.json``. The files are authored
content, reviewed as diffs, so they are read-only here; your reading
status, notes and PDFs live in the research store (D42), keyed by the same
paper ids.
"""

from __future__ import annotations

import json
from pathlib import Path

import quant_hub.data
from quant_hub.data.literature import Canon, CanonError, canon_from_dicts

CANON_DIR = Path(quant_hub.data.__file__).parent / "canon"


def load_canon(directory: str | Path = CANON_DIR) -> Canon:
    """The checked canon. Raises ``CanonError`` naming every problem,
    including a strand listed in ``timeline.json`` without its file."""
    directory = Path(directory)
    try:
        timeline = json.loads((directory / "timeline.json").read_text(encoding="utf-8"))
        papers = {s["id"]: json.loads((directory / f"{s['id']}.json").read_text(encoding="utf-8"))
                  for s in timeline["strands"]}
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise CanonError([f"could not read the canon in {directory}: {exc}"]) from exc
    return canon_from_dicts(timeline, papers)
