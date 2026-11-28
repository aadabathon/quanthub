"""Console output that can't crash a command line (D113).

On Windows a redirected stdout, like the scheduled run's log, is in the
locale's code page, usually cp1252. There a β, a typographic minus or an
arrow doesn't exist, and printing one raises ``UnicodeEncodeError`` partway
through a run: after the snapshot, before the backup. Every command line
calls ``safe_console`` first, so a character the console can't show prints
as "?" instead.
"""

from __future__ import annotations

import sys


def safe_console() -> None:
    """Make stdout and stderr replace what their encoding can't show."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")
