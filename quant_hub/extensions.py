"""Extensions: pages and commands from other installed packages, found at
startup (D86, D87).

Quant Hub can be extended without editing it. A package declares an entry
point in the ``quant_hub.extensions`` group of its ``pyproject.toml``::

    [project.entry-points."quant_hub.extensions"]
    mine = "my_package.app:contribute"

``contribute(core)`` receives the wired ``Core`` and returns an
``Extension``: the pages to add to the sidebar, the Ctrl+K commands to add,
and a ``shutdown`` that closes whatever the extension opened. ``app.py``
calls ``load_extensions`` after ``build_core``, then ``merge_pages`` and
``merge_commands`` before it builds the window.

A command is a ``Callable[[], None]`` that Ctrl+K runs on the GUI thread,
so anything slow submits itself to ``core.tasks``. A name the window
already uses, or one an earlier extension took, is refused and reported.

The rules:

* **Discovery is the standard library's** (``importlib.metadata``). An
  editable install carries its entry points, so ``pip install -e`` is all
  an extension needs. There's no new dependency.
* **An extension never stops the app.** A problem is logged and reported,
  and only the extension or page at fault is skipped. Problems are an entry
  point that won't load, a ``contribute`` that raises or returns something
  else, and a page title that's already taken.
* **Quant Hub never names an extension.** Its modules import only the
  standard library, ``quant_hub`` and its declared dependencies, which
  ``tests/test_layering.py`` checks. The dependency points one way: an
  extension uses Quant Hub, never the reverse.
* **The off switch:** ``[extensions] enabled = false`` in settings, or
  ``QUANT_HUB_NO_EXTENSIONS=1`` for one run. The test suite sets the
  variable (``tests/conftest.py``), so it tests the app a fresh clone gets.

Where a page goes:

* **Its section:** ``None`` is the top group. A name such as
  ``"Research"`` is that section, and a new name adds a section after the
  others.
* **Its place in the section:** after the page named in ``after``, or at
  the section's end. An ``after`` that names no page there also puts it at
  the end, and is reported.
* **Titles are unique.** A title keys the sidebar, the workspace's docks
  and the saved layout, so one that's already taken is refused.

What an extension gets is the whole ``Core``: every service, store and the
``TaskRunner``. Its pages follow the GUI rules (AGENTS.md hard rule 3):
blocking work goes through ``core.tasks``. There's no frozen interface in
between, so an extension built against one version of Quant Hub may need
changes for the next.

This module is toolkit-free. A page factory is opaque here, a
``Callable[[Shell], Page]`` as in ``gui/main_window.py``, so it imports no
Qt and is tested without a display.

Lifecycle: ``contribute`` runs once, after the core is built and before any
window exists. ``shutdown`` runs once at quit, after the pages have saved
their state and after ``Core.shutdown``, so the task worker has stopped
before an extension closes what its jobs might use.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import metadata
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from quant_hub.config import AppConfig
    from quant_hub.core import Core

ENTRY_POINT_GROUP = "quant_hub.extensions"
DISABLE_ENV = "QUANT_HUB_NO_EXTENSIONS"

_logger = logging.getLogger(__name__)

# A page factory is gui.main_window.PageFactory, Callable[[Shell], Page]:
# opaque here so this module stays free of Qt.
PageFactory = Callable[[Any], Any]
# gui.main_window.PageSections, with the same opaque factory.
Sections = list[tuple[str | None, list[tuple[str, str, PageFactory]]]]


def _nothing() -> None:
    """The default shutdown: an extension that opens nothing closes nothing."""


@dataclass(frozen=True)
class PageSpec:
    """One page an extension adds."""

    title: str                    # the sidebar label; unique across all pages
    icon: str                     # a qtawesome icon name, e.g. "ph.notebook"
    factory: PageFactory          # builds the page, given the shell
    section: str | None = None    # None: the top group
    after: str | None = None      # the page to follow; None: the section's end

    def __post_init__(self) -> None:
        if not isinstance(self.title, str) or not self.title.strip():
            raise ValueError("a page needs a title")
        if not isinstance(self.icon, str) or not self.icon.strip():
            raise ValueError(f"page {self.title!r} needs an icon name")
        if not callable(self.factory):
            raise ValueError(f"page {self.title!r} needs a factory")


@dataclass(frozen=True)
class Extension:
    """What ``contribute(core)`` returns."""

    name: str
    pages: Sequence[PageSpec] = ()
    shutdown: Callable[[], None] = _nothing
    # Ctrl+K commands, name -> what to run (on the GUI thread; slow work
    # goes through core.tasks).
    commands: Mapping[str, Callable[[], None]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("an extension needs a name")
        if not all(isinstance(page, PageSpec) for page in self.pages):
            raise ValueError(f"extension {self.name!r}: pages must be PageSpecs")
        if not callable(self.shutdown):
            raise ValueError(f"extension {self.name!r}: shutdown must be callable")
        for command, run in self.commands.items():
            if not isinstance(command, str) or not command.strip():
                raise ValueError(f"extension {self.name!r}: a command needs a name")
            if not callable(run):
                raise ValueError(f"extension {self.name!r}: command {command!r} isn't callable")


class EntryPointLike(Protocol):
    """What ``load_extensions`` uses from ``importlib.metadata.EntryPoint``."""

    name: str

    def load(self) -> Any: ...


def extensions_wanted(config: AppConfig) -> bool:
    """Whether to look for extensions at all: on in settings, and not turned
    off for this run by ``QUANT_HUB_NO_EXTENSIONS`` (any value but ``0``)."""
    switch = os.environ.get(DISABLE_ENV, "").strip()
    return config.extensions_enabled and switch in ("", "0")


def _discover() -> Iterable[EntryPointLike]:
    return metadata.entry_points(group=ENTRY_POINT_GROUP)


def load_extensions(core: Core,
                    entry_points: Iterable[EntryPointLike] | None = None,
                    ) -> tuple[list[Extension], list[str]]:
    """Each installed extension's contribution, and every problem found.

    ``entry_points`` defaults to those installed in the group; tests pass
    their own. Nothing raised by an extension escapes.
    """
    found = list(_discover() if entry_points is None else entry_points)
    extensions: list[Extension] = []
    problems: list[str] = []
    for entry in found:
        label = getattr(entry, "name", repr(entry))
        try:
            contribute = entry.load()
        except Exception as exc:
            problems.append(f"{label}: could not load ({type(exc).__name__}: {exc})")
            continue
        if not callable(contribute):
            problems.append(f"{label}: its entry point isn't callable")
            continue
        try:
            extension = contribute(core)
        except Exception as exc:
            problems.append(f"{label}: contribute failed ({type(exc).__name__}: {exc})")
            continue
        if not isinstance(extension, Extension):
            problems.append(f"{label}: contribute returned {type(extension).__name__},"
                            " not an Extension")
            continue
        extensions.append(extension)
    return extensions, problems


def merge_pages(sections: Sections,
                extensions: Iterable[Extension]) -> tuple[Sections, list[str]]:
    """The app's sections with every extension's pages placed in them.

    The input is left as it is. A page whose title is taken is skipped and
    reported; one whose ``after`` isn't in its section goes at the end, and
    that's reported too.
    """
    merged: Sections = [(title, list(items)) for title, items in sections]
    taken = {name for _, items in merged for name, _, _ in items}
    problems: list[str] = []
    for extension in extensions:
        for page in extension.pages:
            if page.title in taken:
                problems.append(f"{extension.name}: a page named {page.title!r} already"
                                " exists, so this one is skipped")
                continue
            items = next((items for title, items in merged if title == page.section), None)
            if items is None:
                items = []
                merged.append((page.section, items))
            names = [name for name, _, _ in items]
            if page.after is not None and page.after not in names:
                problems.append(f"{extension.name}: {page.title!r} follows {page.after!r},"
                                " which isn't in its section, so it goes at the end")
            at = names.index(page.after) + 1 if page.after in names else len(items)
            items.insert(at, (page.title, page.icon, page.factory))
            taken.add(page.title)
    return merged, problems


def merge_commands(extensions: Iterable[Extension]
                   ) -> tuple[dict[str, Callable[[], None]], list[str]]:
    """Every extension's commands in one map, and every name refused. The
    first extension to use a name keeps it; the window refuses names it
    already uses itself (``MainWindow``)."""
    merged: dict[str, Callable[[], None]] = {}
    owner: dict[str, str] = {}
    problems: list[str] = []
    for extension in extensions:
        for name, run in extension.commands.items():
            if name in merged:
                problems.append(f"{extension.name}: the command {name!r} is already"
                                f" {owner[name]}'s, so this one is skipped")
                continue
            merged[name], owner[name] = run, extension.name
    return merged, problems


def shut_down(extensions: Iterable[Extension]) -> None:
    """Each extension's ``shutdown``, guarded so one failure can't skip the
    rest."""
    for extension in extensions:
        try:
            extension.shutdown()
        except Exception:
            _logger.exception("error shutting down extension %s", extension.name)
