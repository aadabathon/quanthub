"""Desk: every voice's view of your book, side by side, in dollars (D3).

The page on ``services/desk.py``. It reads the stored snapshots (D1): it
computes no claim, and nothing on it can change one. The dollars come from
``models/comparison.py``: each claim on the book's return times the book's
value.

**The main column**, all for the horizon chosen at the top (5, 21 or 63
sessions):
- **The voices, side by side:** each voice's outcomes for the book in
  dollars: 90% of them (the thin line), the middle half (the bar), the
  median (the tick). The baseline, the no-edge voice, is grey: every other
  voice differs from it in one assumption. The table gives the five
  numbers, and each voice's worst 1 in 20 against the baseline's.
- **Where they agree and disagree:** every claim the voices share, from
  the lowest to the highest, and how far apart, in dollars, in chances of a
  fall, and in volatility. Nothing is averaged.
- **What each would do:** each voice's proposal in dollars: what it would
  sell or buy, and what goes to cash. For a proposal that scales the whole
  book, what it changes against holding under every voice's claims: what
  the worst 1 in 20 saves, what the median and the best 1 in 20 give up.

**The side panel:**
- **Snapshot:** which stored snapshot (the newest unless you pick one), its
  day, prices, value, what was left out, and its hash. **Take a snapshot
  now** does what the daily run does, from the chosen prices; there's one
  per data day, so a second take on the same prices stores nothing.
- **The book:** each priced holding, its value and weight, and the range
  the baseline gives it over 21 sessions.
- **Why:** a voice's reasoning in its own words, the parameters it ran
  with, and the papers it rests on, from the canon (the Research page's
  timeline). Select a paper to read what it changed, and open its source.

Loading a snapshot is one TaskRunner job: a few milliseconds, plus reading
the canon once. Taking one fetches every holding's closes, so it takes as
long as the price source needs (seconds per symbol on a free plan, the
first time in a day).
"""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from quant_hub.data.desk import LEVELS
from quant_hub.data.literature import CanonPaper
from quant_hub.gui.charts import BandRow, QuantileBands
from quant_hub.gui.components import (
    Card,
    CardGrid,
    DataTable,
    Note,
    PageHeader,
    SegmentedControl,
    SelectField,
    StatTable,
    button,
    form_grid,
)
from quant_hub.gui.pages.base import ScrollPage, saved_grid
from quant_hub.gui.theme import PALETTE, SIDE_PANEL_WIDTH, SPACE_L
from quant_hub.models.baseline import HORIZONS
from quant_hub.models.comparison import BASELINE, Across, Comparison, Proposed
from quant_hub.services.desk import DeskService, DeskView, Taken

_NOTE = SIDE_PANEL_WIDTH - 2 * SPACE_L
_ROW = 30
_STATE_KEY = "desk.view"
_EMPTY = ("No snapshot yet. Take one here, or let the daily run take it "
          "(python -m quant_hub.snapshot, after the close).")
_TAKE = ("The daily run takes one after the close. Taking one now stores today's book if "
         "it's new: on the same prices, nothing new is stored.")
_LEVEL_NAMES = {0.05: "Worst 1 in 20", 0.25: "Lower quarter", 0.5: "Median",
                0.75: "Upper quarter", 0.95: "Best 1 in 20"}


def _money(x: float | None) -> str:
    if x is None:
        return "—"
    if abs(x) < 0.5:
        return "$0"
    return f"{'+' if x >= 0 else '−'}${abs(x):,.0f}"


def _compact(x: float) -> str:
    sign = "+" if x >= 0 else "−"
    x = abs(x)
    for threshold, suffix in ((1e6, "M"), (1e3, "K")):
        if x >= threshold:
            return f"{sign}${x / threshold:.1f}".rstrip("0").rstrip(".") + suffix
    return f"{sign}${x:,.0f}"


def _ink(x: float) -> str:
    if abs(x) < 0.5:
        return PALETTE.text_secondary
    return PALETTE.good if x > 0 else PALETTE.critical


def _horizon_label(h: int) -> str:
    return f"{h} sessions"


class DeskPage(ScrollPage):
    def __init__(self, desk: DeskService, sources: list[str], default_source: str, tasks,
                 state_store) -> None:
        super().__init__()
        self._desk, self._tasks, self._state_store = desk, tasks, state_store
        saved = state_store.load(_STATE_KEY) or {}
        self._horizon = saved.get("horizon") if saved.get("horizon") in HORIZONS else 21
        self._chosen: int | None = None          # None: the newest snapshot
        self._view: DeskView | None = None
        self._ids: dict[str, int] = {}
        self._papers: tuple[CanonPaper, ...] = ()
        self._header = PageHeader("Desk", "Every model's view of your book, side by side, "
                                          "in dollars")
        self.body.addWidget(self._header)
        grid = CardGrid()
        grid.add_column([("voices", self._build_voices(), None),
                         ("agree", self._build_agree(), None),
                         ("proposals", self._build_proposals(), None)], x=0, w=8)
        grid.add_column([("snapshot", self._build_snapshot(sources, default_source), None),
                         ("book", self._build_book(), None),
                         ("why", self._build_why(), None)], x=8, w=4,
                        min_width=SIDE_PANEL_WIDTH)
        self._grid = saved_grid(grid, state_store, "desk.cards")
        self.body.addWidget(grid, 1)

    # ------------------------------------------------------------ building
    def _build_voices(self) -> Card:
        self._voices_card = Card("The voices, side by side", "The book's change in dollars, "
                                                             "by each voice's claims")
        self._horizon_control = SegmentedControl([_horizon_label(h) for h in HORIZONS],
                                                 _horizon_label(self._horizon))
        self._horizon_control.changed.connect(self._set_horizon)
        self._voices_card.add_action(self._horizon_control)
        self._bands = QuantileBands("money", _money)
        self._voices_card.body.addWidget(self._bands)
        self._band_table = DataTable([("Voice", "left"), ("5%", "right"), ("25%", "right"),
                                      ("Median", "right"), ("75%", "right"), ("95%", "right"),
                                      ("5% vs baseline", "right")], row_height=_ROW)
        self._band_table.balance_columns()
        self._band_table.setMinimumHeight(4 * _ROW + 36)
        self._voices_card.body.addWidget(self._band_table)
        return self._voices_card

    def _build_agree(self) -> Card:
        self._agree_card = Card("Where they agree and disagree", "Every claim the voices share, "
                                                                 "lowest to highest; nothing "
                                                                 "averaged")
        self._agree = DataTable([("Claim", "left"), ("Lowest", "right"), ("Highest", "right"),
                                 ("Apart", "right")], row_height=_ROW)
        self._agree.balance_columns()
        self._agree.setMinimumHeight(5 * _ROW + 36)
        self._agree_card.body.addWidget(self._agree)
        return self._agree_card

    def _build_proposals(self) -> Card:
        self._proposals_card = Card("What each would do", "Each voice's proposal in dollars, and "
                                                          "what it changes under every voice's "
                                                          "claims")
        self._proposals = DataTable([("Voice", "left"), ("Would", "left"), ("Sells", "right"),
                                     ("Buys", "right"), ("To cash", "right")], row_height=_ROW)
        self._proposals.balance_columns()
        self._proposals.setMinimumHeight(4 * _ROW + 36)
        self._proposals_card.body.addWidget(self._proposals)
        self._effects = DataTable([("Proposal", "left"), ("Under", "left"),
                                   ("Worst 1 in 20", "right"), ("Median", "right"),
                                   ("Best 1 in 20", "right")], row_height=_ROW)
        self._effects.balance_columns()
        self._effects.setMinimumHeight(4 * _ROW + 36)
        self._proposals_card.body.addWidget(self._effects)
        self._effects_note = Note("ph.info", "info")
        self._effects_note.set_text("Each cell is what the proposal changes against holding, read "
                                    "under that voice's claims; + is better for you. Scaling the "
                                    "book by m scales every outcome by m exactly, so a voice "
                                    "with no drift sees no cost at the median.")
        self._proposals_card.body.addWidget(self._effects_note)
        return self._proposals_card

    def _build_snapshot(self, sources: list[str], default_source: str) -> Card:
        card = Card("Snapshot", "What the desk said, frozen; the newest unless you pick one")
        self._snapshot_select = SelectField("Snapshot", [], None, _NOTE)
        self._snapshot_select.select.current_changed.connect(self._pick)
        card.body.addWidget(self._snapshot_select)
        self._facts = StatTable([("The snapshot", [
            ("as_of", "Prices as of"), ("taken", "Taken"), ("source", "Prices from"),
            ("value", "Book value"), ("priced", "Holdings priced"), ("units", "Units as of"),
            ("voices", "Voices"), ("sha", "SHA-256")])])
        card.body.addWidget(self._facts)
        self._left_out = Note("ph.warning", "warning", width=_NOTE)
        self._left_out.hide()
        card.body.addWidget(self._left_out)
        choices = sources or [default_source]
        self._take_source = SelectField("Prices from", choices,
                                        default_source if default_source in choices
                                        else choices[0], _NOTE // 2)
        card.body.addLayout(form_grid([self._take_source], 1))
        self._take_button = button("Take a snapshot now", "primary", "ph.camera",
                                   on_click=self.take)
        self._take_button.setToolTip("What the daily run does: today's book, every voice's "
                                     "claims, stored once and written as JSON. One per data "
                                     "day.")
        card.body.addWidget(self._take_button)
        self._take_note = Note("ph.info", "info", width=_NOTE)
        self._take_note.set_text(_EMPTY)
        self._take_reported = False      # the note shows a take's result, once there is one
        card.body.addWidget(self._take_note)
        return card

    def _build_book(self) -> Card:
        self._book_card = Card("The book", "Priced holdings; the range is the baseline's, "
                                           "21 sessions, 90%")
        self._book = DataTable([("Symbol", "left"), ("Value", "right"), ("Weight", "right"),
                                ("Range", "right")], row_height=_ROW)
        self._book.balance_columns()
        self._book.setMinimumHeight(6 * _ROW + 36)
        self._book_card.body.addWidget(self._book)
        return self._book_card

    def _build_why(self) -> Card:
        card = Card("Why", "Each voice's reasoning and the papers it rests on")
        self._why_select = SelectField("Voice", [], None, _NOTE // 2)
        self._why_select.select.current_changed.connect(self._show_why)
        card.body.addWidget(self._why_select)
        self._why = Note("ph.chat-text", "info", width=_NOTE)
        card.body.addWidget(self._why)
        self._parameters = DataTable([("Parameter", "left"), ("Value", "right")],
                                     row_height=_ROW)
        self._parameters.balance_columns()
        self._parameters.setMinimumHeight(3 * _ROW + 36)
        card.body.addWidget(self._parameters)
        self._paper_table = DataTable([("Paper", "left"), ("Title", "left")], row_height=_ROW)
        self._paper_table.balance_columns()
        self._paper_table.setMinimumHeight(2 * _ROW + 36)
        self._paper_table.selection_changed.connect(self._show_paper)
        card.body.addWidget(self._paper_table)
        self._paper = Note("ph.book-open", "info", width=_NOTE)
        self._paper.hide()
        card.body.addWidget(self._paper)
        self._open_paper = button("Open source", icon_name="ph.arrow-square-out",
                                  on_click=self._open_source)
        self._open_paper.setEnabled(False)
        card.body.addWidget(self._open_paper)
        return card

    # ------------------------------------------------------------ loading
    def on_show(self) -> None:
        # Every show: the daily run may have stored a snapshot meanwhile.
        self.reload()

    def reload(self) -> None:
        chosen = self._chosen
        self._tasks.submit(lambda: self._desk.view(chosen), on_done=self._render,
                           on_error=self._error)

    def _error(self, exc: Exception) -> None:
        self._take_button.setEnabled(True)
        self._header.set_status(str(exc), error=True)

    def _pick(self, text: str) -> None:
        snapshot_id = self._ids.get(text)
        if snapshot_id is None or (self._view is not None and snapshot_id == self._view.id):
            return
        newest = self._view is not None and snapshot_id == self._view.snapshots[0].id
        self._chosen = None if newest else snapshot_id
        self.reload()

    def take(self) -> None:
        """Take today's snapshot on the worker thread (module docstring)."""
        source = self._take_source.current()
        self._take_button.setEnabled(False)
        self._header.set_status(f"Taking a snapshot from {source}: every holding's closes, "
                                "then every voice…")
        self._tasks.submit(lambda: self._desk.take(source), on_done=self._taken,
                           on_error=self._error)

    def _taken(self, taken: Taken) -> None:
        self._take_button.setEnabled(True)
        silent = " ".join(taken.silent)
        if taken.path is None and taken.silent and taken.silent[0].startswith("nothing new"):
            text = f"Nothing new: this book on these prices is already snapshot #{taken.id}."
        else:
            text = f"Snapshot #{taken.id} stored" + (f", written to {taken.path}" if taken.path
                                                     else "") + "."
            if silent:
                text += f" Silent: {silent}"
        self._take_note.set_text(text)
        self._take_reported = True
        self._chosen = None
        self.reload()

    # ---------------------------------------------------------- rendering
    def _render(self, view: DeskView | None) -> None:
        self._view = view
        if view is None:
            self._header.set_status("No snapshot yet: take one from the side panel.")
            self._take_note.set_text(_EMPTY)
            for table in (self._band_table, self._agree, self._proposals, self._effects,
                          self._book, self._parameters, self._paper_table):
                table.set_rows([])
            self._bands.clear()
            return
        if not self._take_reported:
            self._take_note.set_text(_TAKE)
        labels = []
        self._ids = {}
        for s in view.snapshots:
            text = f"#{s.id} · as of {s.as_of:%b %d} · taken {s.taken_at:%b %d %H:%M}"
            self._ids[text] = s.id
            labels.append(text)
        current = next(t for t, i in self._ids.items() if i == view.id)
        self._snapshot_select.select.blockSignals(True)
        self._snapshot_select.select.set_items(labels, current)
        self._snapshot_select.select.blockSignals(False)
        self._render_facts(view)
        self._render_book(view)
        voices = [v.voice for v in view.snapshot.voices]
        shown = self._why_select.current()
        self._why_select.select.blockSignals(True)
        self._why_select.select.set_items(voices, shown if shown in voices else
                                          (voices[0] if voices else None))
        self._why_select.select.blockSignals(False)
        self._show_why(self._why_select.current())
        self._render_horizon()
        snapshot = view.snapshot
        self._header.set_status(f"Snapshot #{view.id}, prices as of {snapshot.as_of:%b %d, %Y}: "
                                f"{len(snapshot.voices)} voices on {len(snapshot.book.holdings)} "
                                "holdings." + ("" if view.comparison else f" {view.problem}"))

    def _render_facts(self, view: DeskView) -> None:
        s, book, facts = view.snapshot, view.snapshot.book, self._facts
        facts.set_value("as_of", f"{s.as_of:%a %b %d, %Y}")
        facts.set_value("taken", f"{s.taken_at:%b %d, %Y %H:%M}")
        facts.set_value("source", s.source)
        facts.set_value("value", f"${book.value:,.0f}" if book.value >= 0
                        else f"−${-book.value:,.0f}")
        held = len(book.holdings) + len(book.unpriced)
        facts.set_value("priced", f"{len(book.holdings)} of {held}")
        facts.set_value("units", f"{book.units_as_of:%b %d}, after the last prices"
                        if book.units_as_of else "the same day")
        facts.set_value("voices", ", ".join(v.voice for v in s.voices) or "none")
        facts.set_value("sha", s.sha256[:16])
        if book.unpriced:
            self._left_out.set_text("Left out, never guessed: " + "; ".join(book.unpriced))
        self._left_out.setVisible(bool(book.unpriced))

    def _render_book(self, view: DeskView) -> None:
        book = view.snapshot.book
        weights = book.weights()
        rows = []
        for h in sorted(book.holdings, key=lambda h: -abs(h.value)):
            band = view.holdings.get(h.symbol)
            rows.append([h.symbol, f"${h.value:,.0f}", f"{weights.get(h.symbol, 0.0):.1%}",
                         f"{_compact(band[0])} to {_compact(band[-1])}" if band else "—"])
        if book.cash:
            rows.append(["Cash", _money(book.cash), f"{book.cash / book.value:.1%}"
                         if book.value else "—", ""])
        self._book.set_rows(rows)

    def _set_horizon(self, text: str) -> None:
        self._horizon = int(text.split()[0])
        self._state_store.save(_STATE_KEY, {"horizon": self._horizon})
        self._render_horizon()

    def _render_horizon(self) -> None:
        view = self._view
        comparison = view.comparison if view is not None else None
        h = self._horizon
        if comparison is None:
            for table in (self._band_table, self._agree, self._proposals, self._effects):
                table.set_rows([])
            self._bands.clear()
            return
        self._render_bands(comparison, h)
        self._render_agree(comparison, h)
        self._render_proposals(comparison, h)

    def _render_bands(self, c: Comparison, h: int) -> None:
        rows, table = [], []
        base = c.band(BASELINE, h)
        for voice in c.voices:
            band = c.band(voice, h)
            if not band:
                continue
            color = PALETTE.muted if voice == BASELINE else PALETTE.series
            rows.append(BandRow(voice, band, color))
            versus = "" if voice == BASELINE or not base else _money(band[0] - base[0])
            table.append([voice, *(_money(x) for x in band), versus])
        self._bands.set_rows(rows)
        self._band_table.set_rows(table)
        self._voices_card.set_subtitle(f"The book's change over {h} sessions, in dollars, from "
                                       f"${c.value:,.0f}: each voice's claims")

    def _render_agree(self, c: Comparison, h: int) -> None:
        rows = []
        shown = [a for a in c.across if a.horizon == h and
                 (a.kind != "dollars" or a.level in (0.05, 0.5, 0.95)) and
                 (a.kind != "vol" or a.level == 0.5)]
        for a in shown:
            (lo_voice, lo), (hi_voice, hi) = a.low, a.high
            one = len(a.values) == 1
            rows.append([self._claim_name(a, c), f"{self._value(a, c, lo)} · {lo_voice}",
                         "" if one else f"{self._value(a, c, hi)} · {hi_voice}",
                         "one voice" if one else self._apart(a, c)])
        self._agree.set_rows(rows)
        worst = next((a for a in shown if a.kind == "dollars" and a.level == 0.05), None)
        if worst is not None and len(worst.values) > 1:
            self._agree_card.set_subtitle(f"Over {h} sessions the voices' worst 1 in 20 runs "
                                          f"from {_money(worst.low[1])} to "
                                          f"{_money(worst.high[1])}; nothing averaged")

    @staticmethod
    def _claim_name(a: Across, c: Comparison) -> str:
        if a.kind == "dollars":
            return f"{_LEVEL_NAMES[a.level]}, {a.horizon} sessions"
        if a.kind == "vol":
            return f"Volatility, {a.horizon} sessions (median)"
        op, _, k = a.event.partition(" ")
        drop = float(k)
        if op in ("<=", "<") and drop < 0:
            return f"Chance of a {-drop:.0%} fall ({_money(c.value * drop)})"
        return f"Chance the return is {a.event}"

    @staticmethod
    def _value(a: Across, c: Comparison, x: float) -> str:
        if a.kind == "dollars":
            return _money(x)
        if a.kind == "vol":
            return f"{x:.1%} (~${c.daily_move(x):,.0f}/day)"
        return f"{x:.1%}"

    @staticmethod
    def _apart(a: Across, c: Comparison) -> str:
        if a.kind == "dollars":
            return f"${a.apart:,.0f}"
        if a.kind == "vol":
            return f"{a.apart:.1%}"
        return f"{a.apart:.1%} pts"

    def _render_proposals(self, c: Comparison, h: int) -> None:
        rows = []
        for p in c.proposals:
            sells = sum(t.dollars for t in p.trades if t.dollars < 0)
            buys = sum(t.dollars for t in p.trades if t.dollars > 0)
            rows.append([p.voice, p.note, _money(sells) if sells else "",
                         _money(buys) if buys else "", _money(p.to_cash) if p.trades else ""])
        self._proposals.set_rows(rows)
        effects = [e for e in c.effects if e.horizon == h]
        cells = []
        for e in effects:
            change = dict(zip(LEVELS, e.change))
            cells.append([e.proposal, e.law, *((_money(change[t]), _ink(change[t]))
                                               for t in (0.05, 0.5, 0.95))])
        mixed = [p.voice for p in c.proposals if not p.hold and p.scale is None]
        cells += [[voice, "changes the mix: its effect isn't in these claims", "", "", ""]
                  for voice in mixed]
        self._effects.set_rows(cells)
        self._effects.setMinimumHeight(max(4, len(cells)) * _ROW + 36)    # every row, no scroll
        acting = [p for p in c.proposals if not p.hold]
        self._effects.setVisible(bool(acting))
        self._effects_note.setVisible(bool(acting))
        self._proposals_card.set_subtitle(
            "Every voice holds today" if not acting else
            f"{len(acting)} of {len(c.proposals)} would change the book; below, what each "
            f"changes over {h} sessions")

    def _show_why(self, voice: str) -> None:
        view = self._view
        output = (next((v for v in view.snapshot.voices if v.voice == voice), None)
                  if view is not None else None)
        if output is None:
            self._why.set_text("")
            self._parameters.set_rows([])
            self._paper_table.set_rows([])
            return
        self._why.set_text(output.why or "This voice gave no reasoning.")
        self._parameters.set_rows([[name, self._parameter(value)]
                                   for name, value in output.parameters.items()])
        self._papers = view.papers.get(voice, ())
        self._paper_table.set_rows([[p.label, p.title] for p in self._papers])
        if self._papers:
            self._paper_table.selectRow(0)
        else:
            self._show_paper(-1)

    @staticmethod
    def _parameter(value) -> str:
        if isinstance(value, float):
            return f"{value:.6g}"
        if isinstance(value, (list, tuple)):
            return ", ".join(f"{v:.4g}" if isinstance(v, float) else str(v) for v in value)
        return str(value)

    def _show_paper(self, row: int) -> None:
        if not 0 <= row < len(self._papers):
            self._paper.hide()
            self._open_paper.setEnabled(False)
            return
        p = self._papers[row]
        parts = [f"{p.label}, {p.venue}."]
        if p.question:
            parts.append(f"Asked: {p.question}")
        parts.append(f"Changed: {p.changed}")
        if p.assumed:
            parts.append(f"Assumed: {p.assumed}")
        self._paper.set_text(" ".join(parts))
        self._paper.show()
        self._open_paper.setEnabled(bool(p.source_url))

    def _open_source(self) -> None:
        row = self._paper_table.selected_row()
        if 0 <= row < len(self._papers):
            url = QUrl(self._papers[row].source_url)
            if url.scheme() in ("https", "http") and url.host():
                QDesktopServices.openUrl(url)
