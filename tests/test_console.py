"""Command lines survive a console that can't show every character (D113)."""

import io
import sys

from quant_hub.console import safe_console


def cp1252(monkeypatch):
    """stdout and stderr as a redirected Windows console: cp1252, strict."""
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    err = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    return out, err


def read(stream) -> str:
    stream.flush()
    return stream.buffer.getvalue().decode("cp1252")


def test_an_unshowable_character_prints_as_a_question_mark(monkeypatch):
    out, _ = cp1252(monkeypatch)
    safe_console()
    print("the book at \u03b2 0.83 \u2192 0.89")
    assert read(out).strip() == "the book at ? 0.83 ? 0.89"


def test_the_daily_run_finishes_on_such_a_console(monkeypatch):
    from quant_hub import snapshot as cli
    out, err = cp1252(monkeypatch)

    class Desk:
        runs = []

        def resolve(self):
            raise ValueError("no bars for the \u03b2 fit")

        def take(self, source):
            raise ValueError("the tail voice wants \u03b2 \u2212 1")

        def record_run(self, run):
            self.runs.append(run)

    class Core:
        config = type("Config", (), {"ibkr_enabled": False, "massive_key": ""})()
        desk = Desk()

        def shutdown(self):
            pass
    monkeypatch.setattr(cli, "build_core", Core)
    assert cli.main(["--no-backup"]) == 1                     # failed, but finished
    assert "no snapshot: the tail voice wants ? ? 1" in read(err)
    (run,) = Core.desk.runs                                   # and recorded itself
    assert run.problems[1] == "no snapshot: the tail voice wants \u03b2 \u2212 1"
