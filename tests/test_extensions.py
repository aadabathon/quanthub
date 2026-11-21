"""Extensions (D86, D87): loading, problems, placement, commands, the off
switch, shutdown, and an extension's page and command reaching the window."""

import os

import pytest

from quant_hub import extensions as ext
from quant_hub.config import AppConfig
from quant_hub.extensions import (
    DISABLE_ENV,
    Extension,
    PageSpec,
    extensions_wanted,
    load_extensions,
    merge_commands,
    merge_pages,
    shut_down,
)


def _factory(_shell):
    return None


class EntryPoint:
    """A stand-in for ``importlib.metadata.EntryPoint``: ``load`` returns the
    target, or raises it when it's an exception."""

    def __init__(self, name, target):
        self.name = name
        self._target = target

    def load(self):
        if isinstance(self._target, Exception):
            raise self._target
        return self._target


SECTIONS = [
    (None, [("Dashboard", "ph.house", _factory), ("Markets", "ph.chart-bar", _factory)]),
    ("Research", [("GARCH Lab", "ph.activity", _factory)]),
]


def names(sections):
    return [(title, [name for name, _, _ in items]) for title, items in sections]


def page(title, section=None, after=None):
    return PageSpec(title, "ph.house", _factory, section=section, after=after)


class TestLoading:
    def test_contribute_gets_the_core(self):
        seen = []

        def contribute(core):
            seen.append(core)
            return Extension("mine", (page("Mine"),))

        extensions, problems = load_extensions("the core", [EntryPoint("mine", contribute)])
        assert seen == ["the core"]
        assert [e.name for e in extensions] == ["mine"] and problems == []

    def test_a_broken_extension_is_reported_and_skipped(self):
        def raises(_core):
            raise RuntimeError("no store")

        extensions, problems = load_extensions(None, [
            EntryPoint("unloadable", ImportError("no module named mine")),
            EntryPoint("not-callable", 42),
            EntryPoint("raises", raises),
            EntryPoint("wrong-type", lambda _core: "some pages"),
            EntryPoint("fine", lambda _core: Extension("fine")),
        ])
        assert [e.name for e in extensions] == ["fine"]
        assert [p.split(":")[0] for p in problems] == [
            "unloadable", "not-callable", "raises", "wrong-type"]
        assert "no store" in problems[2] and "not an Extension" in problems[3]

    def test_an_invalid_page_fails_its_contribute(self):
        extensions, problems = load_extensions(None, [EntryPoint(
            "bad", lambda _core: Extension("bad", (PageSpec(" ", "ph.house", _factory),)))])
        assert extensions == [] and "needs a title" in problems[0]

    def test_the_default_is_the_installed_group(self, monkeypatch):
        monkeypatch.setattr(ext, "_discover", lambda: [EntryPoint("x", lambda _c: Extension("x"))])
        extensions, _ = load_extensions(None)
        assert [e.name for e in extensions] == ["x"]

    def test_discovery_reads_the_standard_library(self):
        assert all(hasattr(entry, "load") for entry in ext._discover())


class TestPlacement:
    def test_after_a_page_in_the_top_group(self):
        merged, problems = merge_pages(SECTIONS, [Extension("e", (page("Mine", after="Dashboard"),))])
        assert names(merged)[0] == (None, ["Dashboard", "Mine", "Markets"]) and problems == []

    def test_at_the_end_of_a_named_section(self):
        merged, _ = merge_pages(SECTIONS, [Extension("e", (page("Lab", section="Research"),))])
        assert names(merged)[1] == ("Research", ["GARCH Lab", "Lab"])

    def test_a_new_section_goes_after_the_others(self):
        merged, _ = merge_pages(SECTIONS, [Extension("e", (page("A", section="Mine"),
                                                           page("B", section="Mine")))])
        assert names(merged)[2] == ("Mine", ["A", "B"])

    def test_a_taken_title_is_refused(self):
        merged, problems = merge_pages(SECTIONS, [Extension("e", (page("Markets"),))])
        assert names(merged) == names(SECTIONS)
        assert "already exists" in problems[0]

    def test_a_missing_after_goes_at_the_end_and_is_reported(self):
        merged, problems = merge_pages(SECTIONS, [Extension("e", (page("Mine", after="Gone"),))])
        assert names(merged)[0] == (None, ["Dashboard", "Markets", "Mine"])
        assert "'Gone'" in problems[0]

    def test_the_input_is_left_as_it_is(self):
        before = names(SECTIONS)
        merge_pages(SECTIONS, [Extension("e", (page("Mine"), page("Lab", section="New")))])
        assert names(SECTIONS) == before

    def test_two_extensions_cannot_share_a_title(self):
        merged, problems = merge_pages(SECTIONS, [Extension("first", (page("Mine"),)),
                                                  Extension("second", (page("Mine"),))])
        assert names(merged)[0][1].count("Mine") == 1
        assert problems[0].startswith("second:")


class TestOffSwitch:
    def test_the_suite_runs_without_extensions(self):
        assert os.environ[DISABLE_ENV] == "1"
        assert not extensions_wanted(AppConfig())

    @pytest.mark.parametrize("value, wanted", [(None, True), ("", True), ("0", True),
                                               ("1", False), ("yes", False)])
    def test_the_variable(self, monkeypatch, value, wanted):
        if value is None:
            monkeypatch.delenv(DISABLE_ENV)
        else:
            monkeypatch.setenv(DISABLE_ENV, value)
        assert extensions_wanted(AppConfig()) is wanted

    def test_the_setting(self, monkeypatch):
        monkeypatch.delenv(DISABLE_ENV)
        assert not extensions_wanted(AppConfig(extensions_enabled=False))


class TestCommands:
    def test_commands_merge_and_the_first_extension_keeps_a_name(self):
        first, second = (lambda: None), (lambda: None)
        merged, problems = merge_commands([
            Extension("a", commands={"Run it": first, "Only a": first}),
            Extension("b", commands={"Run it": second, "Only b": second})])
        assert merged == {"Run it": first, "Only a": first, "Only b": second}
        assert problems == ["b: the command 'Run it' is already a's, so this one is skipped"]

    def test_a_command_needs_a_name_and_something_to_run(self):
        with pytest.raises(ValueError, match="a command needs a name"):
            Extension("a", commands={" ": lambda: None})
        with pytest.raises(ValueError, match="isn't callable"):
            Extension("a", commands={"Run it": "not a function"})


def test_shutdown_runs_every_one_even_after_a_failure():
    calls = []

    def fails():
        calls.append("first")
        raise RuntimeError("closing failed")

    shut_down([Extension("first", shutdown=fails),
               Extension("second", shutdown=lambda: calls.append("second"))])
    assert calls == ["first", "second"]


def test_an_extensions_page_reaches_the_window(tmp_path, monkeypatch):
    """The real wiring: discovery, placement after Dashboard, the page built
    on demand, its command (the window's own names win), and its shutdown at
    quit."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from quant_hub.app import create_app
    from quant_hub.gui.pages.placeholder import PlaceholderPage
    from test_gui_smoke import _close

    closed, ran = [], []
    stand_in = Extension(
        "stand-in",
        (PageSpec("Stand-in", "ph.clipboard-text",
                  lambda _shell: PlaceholderPage("Stand-in", "ph.clipboard-text",
                                                 "A page from an extension"),
                  after="Dashboard"),),
        shutdown=lambda: closed.append(True),
        commands={"Stand-in command": lambda: ran.append(True),
                  "Reset layout": lambda: ran.append("taken")})      # the window's own
    monkeypatch.delenv(DISABLE_ENV)
    monkeypatch.setattr(ext, "_discover", lambda: [EntryPoint("stand-in", lambda _core: stand_in)])
    app, window = create_app(AppConfig(db_path=tmp_path / "s.db", ibkr_enabled=False),
                             argv=["test"])
    try:
        window.show()
        assert list(window._factories)[:2] == ["Dashboard", "Stand-in"]
        window.show_page("Stand-in")
        assert "Stand-in" in window._workspace.shown()
        window._top.search._commands["Stand-in command"]()
        assert ran == [True]
        window._top.search._commands["Reset layout"]()             # still the window's
        assert ran == [True]
    finally:
        _close(app, window)
    assert closed == [True]
