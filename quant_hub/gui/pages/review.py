"""Review: the owner's regime, checked against today's book (W7).

The workflow's front door. One button runs the review's job chain on the
TaskRunner (``services/review.py``): the rules from ``REGIME.md``, the
newest export, history, risk, regressions, growth, the gates, valuations,
the checklist, and a journal record. The page shows:

* **Tiles:** fails, warns, gates closed, and the rules version.
* **Checklist:** every rule over what it governs, breaches first. Each row
  has its number, its limit, how far past in standard errors, and why a
  number is missing when it is.
* **Thesis by stream** (section 1): today's breaches, the run, the verdict.
* **Market state** (section 7): the calm regime's filtered probability over
  the lookback, its odds ahead, and the volatility forecast.
* **Days that decide the numbers:** the days the Portfolio page flags
  (D53), each with the mix or holding it decides, and which numbers were
  graded.
* **Journal:** trades drafted from the export before, notes, and trades
  recorded by hand. Nothing here places an order (D12).
* **Past reviews**, newest first.

The side panel names the rules file and the positions folder, ticks the
owner's own indicators, and records a trade.
"""

from __future__ import annotations

import logging
import math

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QVBoxLayout, QWidget

from quant_hub.data.regime import Effect, Grade, Rule, RuleSet
from quant_hub.gui.charts import Panel, Series, StackedTimeSeries
from quant_hub.gui.components import (
    Card,
    DataTable,
    Field,
    MetricTile,
    Note,
    PageHeader,
    SelectField,
    TextArea,
    button,
    form_grid,
    label,
    metric_row,
)
from quant_hub.gui.pages.base import ScrollPage
from quant_hub.gui.theme import PALETTE, SIDE_PANEL_WIDTH, SPACE_L, SPACE_M
from quant_hub.models.regime import Check, Outcome
from quant_hub.services.review import Review, ReviewError, ReviewService
from quant_hub.tasks import TaskRunner

_logger = logging.getLogger(__name__)

_HALF = (SIDE_PANEL_WIDTH - 2 * SPACE_L - SPACE_M) // 2
_FULL = 2 * _HALF + SPACE_M
_ROW = 32
_HEADER = 36
_NO_RULE = "(none)"
_SEVERE = {Grade.FAIL, Effect.BLOCK_ADDS}
_MILD = {Grade.WARN, Effect.SUGGEST_HEDGE}
_PERCENT_KEYS = {"6.hurdle", "8.implied"}         # rates, though their units aren't "%"
# Short names, so a checklist row (name and limit, what it's on, value,
# result) fits the main column at 1280 px; REGIME.md keeps the full labels.
_SHORT = {
    "4.max-weight": "Weight", "4.max-risk-share": "Risk share", "4.volatility": "Volatility",
    "4.beta": "Beta", "4.var": "VaR 95%", "4.es": "ES 99%", "4.drawdown": "Drawdown",
    "4.cash": "Cash", "4.effective-holdings": "Effective holdings", "4.sizing": "Kelly fraction",
    "5.hedge-budget": "Hedge cost", "5.premium": "Premium at risk", "5.net-delta": "Net delta",
    "5.vega": "Vega", "5.short-options": "Short options", "5.expiry": "Days to expiry",
    "6.hurdle": "Growth margin", "6.correlation": "Residual ρ", "6.stream-beta": "Stream β",
    "6.fraction": "Share of optimum", "7.calm": "Calm probability", "7.volatility": "Vol forecast",
    "7.odds": "Calm odds ahead", "8.margin": "Margin of safety", "8.terminal": "Terminal share",
    "8.implied": "Implied growth", "8.missing": "Missing fields",
}
_ON_WIDTH, _RESULT_WIDTH = 130, 180
_NOTE = SIDE_PANEL_WIDTH - 2 * SPACE_L       # a note's width inside a side-panel card


def _name(rule: Rule) -> str:
    if rule.key.startswith("3.size."):
        return "Intended size"
    return _SHORT.get(rule.key, rule.label)


def _item(text: str) -> str:
    return text.replace("stream: ", "")


def _percent(rule: Rule) -> bool:
    return rule.unit.startswith("%") or rule.key in _PERCENT_KEYS or rule.key.startswith("3.size")


def _number(rule: Rule, x: float | None) -> str:
    if x is None or not math.isfinite(x):
        return "—"
    if _percent(rule):
        return f"{x:.1%}"
    if rule.unit.startswith("$"):
        return f"${x:,.0f}"
    if rule.unit in {"count", "fields", "positions", "calendar days", ""}:
        return f"{x:g}"
    return f"{x:.2f}"


def _limit(c: Check) -> str:
    rule = c.rule
    if rule.key == "8.implied":
        return f"≤ {_number(rule, c.bound)}" if c.bound is not None else f"≤ {rule.limit.upper:g}× CAGR"
    lower, upper = rule.limit.lower, rule.limit.upper
    if lower is not None and upper is not None:
        text = f"{_number(rule, lower)} – {_number(rule, upper)}"
    elif upper is not None:
        text = f"≤ {_number(rule, upper)}"
    else:
        text = f"≥ {_number(rule, lower)}"
    return text


def _value(c: Check) -> str:
    m = c.measure
    if m is None:
        return "—"
    text = _number(c.rule, m.estimate)
    if m.error:
        text += f" ± {_number(c.rule, m.error)}"
    return text


def _result(c: Check) -> tuple[str, str]:
    """The outcome, how far past in standard errors against the evidence
    the rule needs, and for missing data, why."""
    needs = f" of {c.rule.evidence:g}" if c.rule.evidence else ""
    if c.outcome is Outcome.BREACH:
        consequence = c.consequence
        color = (PALETTE.critical if consequence in _SEVERE else
                 PALETTE.warning if consequence in _MILD else PALETTE.text_secondary)
        return consequence.value + (f" · {c.z:.1f}{needs} SE" if c.z is not None else ""), color
    if c.outcome is Outcome.NEAR:
        return f"near · {c.z:.1f}{needs} SE", PALETTE.warning
    if c.outcome is Outcome.PASS:
        return ("pass" + (f": {c.reason}" if c.reason else "")), PALETTE.good
    if c.outcome is Outcome.AMBIGUOUS:
        return f"ambiguous: {c.reason}", PALETTE.warning
    return f"no data: {c.reason}", PALETTE.muted


def _severity(c: Check) -> int:
    if c.outcome is Outcome.BREACH:
        return 0 if c.consequence in _SEVERE else 1 if c.consequence in _MILD else 2
    return {Outcome.NEAR: 3, Outcome.AMBIGUOUS: 4, Outcome.MISSING: 5}.get(c.outcome, 6)


class ReviewPage(ScrollPage):
    def __init__(self, review: ReviewService, tasks: TaskRunner) -> None:
        super().__init__()
        self._review = review
        self._tasks = tasks
        self._rules: RuleSet | None = None
        self._last: Review | None = None
        self._ticks: dict[str, QCheckBox] = {}

        self._header = PageHeader("Review", "Your REGIME.md, checked against today's book")
        self.body.addWidget(self._header)
        main = QHBoxLayout()
        main.setSpacing(SPACE_L)
        self.body.addLayout(main, 1)
        column = QVBoxLayout()
        column.setSpacing(SPACE_L)
        main.addLayout(column, 1)
        self._tiles = {"fail": MetricTile("Fails"), "warn": MetricTile("Warns"),
                       "gates": MetricTile("Gates closed"), "rules": MetricTile("Rules version")}
        column.addLayout(metric_row(list(self._tiles.values())))
        column.addWidget(self._build_checklist())
        column.addWidget(self._build_streams())
        column.addWidget(self._build_market())
        column.addWidget(self._build_influence())
        column.addWidget(self._build_journal())
        column.addWidget(self._build_history())

        side_widget = QWidget()
        side_widget.setFixedWidth(SIDE_PANEL_WIDTH)
        side = QVBoxLayout(side_widget)
        side.setSpacing(SPACE_L)
        side.setContentsMargins(0, 0, 0, 0)
        main.addWidget(side_widget)
        side.addWidget(self._build_rules())
        side.addWidget(self._build_indicators())
        side.addWidget(self._build_trade())
        side.addStretch(1)
        self._clear()

    # ------------------------------------------------------------- layout
    def _build_checklist(self) -> Card:
        self._checklist_card = Card("Checklist", "Every rule over what it governs, breaches first")
        self._checklist = DataTable([("Rule and limit", "left"), ("On", "left"),
                                     ("Value", "right"), ("Result", "left")], row_height=_ROW)
        self._checklist.balance_columns()
        self._checklist.fix_column(1, _ON_WIDTH)
        self._checklist.fix_column(3, _RESULT_WIDTH)
        self._checklist_card.body.addWidget(self._checklist)
        return self._checklist_card

    def _build_streams(self) -> Card:
        self._streams_card = Card("Thesis by stream", "Section 1: the growth hurdle, "
                                  "orthogonality and beta creep, and how long a breach has run")
        self._streams = DataTable([("Stream", "left"), ("Breached today", "left"),
                                   ("Run", "right"), ("Verdict", "left")], row_height=_ROW)
        self._streams.balance_columns()
        self._streams.fix_column(1, 2 * _ON_WIDTH)
        self._streams_card.body.addWidget(self._streams)
        return self._streams_card

    def _build_market(self) -> Card:
        self._market_card = Card("Market state", "Section 7, on the benchmark: the HMM's "
                                 "filtered probability, never smoothed")
        self._calm_chart = StackedTimeSeries()
        self._calm_chart.setMinimumHeight(180)
        self._market_card.body.addWidget(self._calm_chart)
        self._market = DataTable([("Gate", "left"), ("Today", "right"), ("Limit", "right"),
                                  ("Result", "left")], row_height=_ROW)
        self._market.balance_columns()
        self._market.fix_column(3, _RESULT_WIDTH)
        self._market_card.body.addWidget(self._market)
        return self._market_card

    def _build_influence(self) -> Card:
        self._influence_card = Card("Days that decide the numbers", "")
        self._influence = DataTable([("Day", "left"), ("Decides", "left"), ("Move", "right"),
                                     ("Share of variance", "right"), ("Robust share", "right")],
                                    row_height=_ROW)
        self._influence.balance_columns()
        self._influence_card.body.addWidget(self._influence)
        return self._influence_card

    def _build_journal(self) -> Card:
        self._journal_card = Card("Journal", "Trades drafted from the export before; notes; "
                                  "trades you recorded by hand")
        self._drafts = DataTable([("Symbol", "left"), ("Before", "right"), ("After", "right"),
                                  ("Change", "right"), ("Price", "right")], row_height=_ROW)
        self._drafts.balance_columns()
        self._drafts.row_activated.connect(self._draft_to_form)
        self._journal_card.body.addWidget(self._drafts)
        self._notes = TextArea(placeholder="Notes on this review")
        self._journal_card.body.addWidget(self._notes)
        row = QHBoxLayout()
        row.addWidget(button("Save notes", icon_name="ph.floppy-disk", on_click=self._save_notes))
        row.addStretch(1)
        self._journal_card.body.addLayout(row)
        self._trades = DataTable([("Recorded", "left"), ("Symbol", "left"), ("Quantity", "right"),
                                  ("Price", "right"), ("Rule", "left"), ("Reason", "left")],
                                 row_height=_ROW)
        self._trades.balance_columns()
        self._trades.fix_column(5, _RESULT_WIDTH)
        self._journal_card.body.addWidget(self._trades)
        return self._journal_card

    def _build_history(self) -> Card:
        self._history_card = Card("Past reviews", "Each kept whole, with the rules it ran under")
        self._history = DataTable([("Run", "left"), ("Rules", "left"), ("Positions as of", "left"),
                                   ("Fails", "right"), ("Warns", "right")], row_height=_ROW)
        self._history.balance_columns()
        self._history_card.body.addWidget(self._history)
        return self._history_card

    def _build_rules(self) -> Card:
        card = Card("Rules")
        card.body.addWidget(label(f"From {self._review.regime_path}", "muted", wrap=True))
        folder = self._review.positions_folder
        card.body.addWidget(label(f"Positions: the newest export in {folder}" if folder else
                                  "Positions: the newest snapshot imported on the Portfolio page",
                                  "muted", wrap=True))
        if folder is None:
            hint = Note("ph.info", tone="info", width=_NOTE)
            hint.set_text('To read exports straight from a folder, add [positions] folder = '
                          '"…" to settings.toml and restart.')
            card.body.addWidget(hint)
        self._version_note = Note("ph.info", tone="info", width=_NOTE)
        self._error_note = Note("ph.warning", tone="critical", width=_NOTE)
        self._run_notes = Note("ph.info", tone="info", width=_NOTE)
        for note in (self._version_note, self._error_note, self._run_notes):
            card.body.addWidget(note)
            note.setVisible(False)
        self._run_button = button("Run review", "primary", "ph.play", on_click=self._run)
        card.body.addWidget(self._run_button)
        return card

    def _build_indicators(self) -> Card:
        self._indicators_card = Card("Your indicators", "Section 7: tick each that's open today")
        self._indicator_box = QVBoxLayout()
        self._indicators_card.body.addLayout(self._indicator_box)
        self._indicators_card.setVisible(False)
        return self._indicators_card

    def _build_trade(self) -> Card:
        card = Card("Record a trade", "One you placed by hand; recorded, never sent")
        self._symbol = Field("Symbol", "", _HALF, uppercase=True)
        self._quantity = Field("Quantity", "", _HALF, placeholder="+ bought, − sold")
        self._trade_price = Field("Price", "", _HALF, placeholder="optional")
        self._trade_rule = SelectField("Serves rule", [_NO_RULE], _NO_RULE, _HALF)
        self._reason = Field("Reason", "", _FULL)
        card.body.addLayout(form_grid([self._symbol, self._quantity, self._trade_price,
                                       self._trade_rule], 2))
        card.body.addWidget(self._reason)
        card.body.addWidget(button("Record trade", icon_name="ph.notebook",
                                   on_click=self._record_trade))
        return card

    # ----------------------------------------------------------- lifecycle
    def on_show(self) -> None:
        self._tasks.submit(self._review.load_rules, on_done=self._on_rules,
                           on_error=self._on_rules_error)
        self._refresh_journal()

    def _on_rules(self, result) -> None:
        rules, _version_id, _new = result
        self._rules = rules
        self._error_note.setVisible(False)
        self._version_note.set_text(
            f"Version {rules.version or '—'}, written {rules.written or '—'}: "
            f"{rules.reason or 'no reason given'}. {len(rules.rules)} rules on, "
            f"{sum(r.sleeve is not None for r in rules.sleeves)} of {len(rules.sleeves)} "
            "holdings in a sleeve.")
        self._version_note.setVisible(True)
        self._tiles["rules"].set_value(rules.version or "—", hint=str(rules.written or ""))
        keys = [_NO_RULE, *(r.key for r in rules.rules)]
        self._trade_rule.select.set_items(keys, _NO_RULE)
        self._set_indicators(rules.market.indicators)

    def _on_rules_error(self, exc: Exception) -> None:
        self._rules = None
        self._error_note.set_text(str(exc))
        self._error_note.setVisible(True)
        self._header.set_status("The rules can't be read.", error=True)

    def _set_indicators(self, names) -> None:
        kept = {name: box.isChecked() for name, box in self._ticks.items()}
        for box in self._ticks.values():
            box.setParent(None)
        self._ticks = {}
        for name in names:
            box = QCheckBox(name)
            box.setChecked(kept.get(name, False))
            self._indicator_box.addWidget(box)
            self._ticks[name] = box
        self._indicators_card.setVisible(bool(names))

    # ---------------------------------------------------------------- run
    def _run(self) -> None:
        ticks = {name: box.isChecked() for name, box in self._ticks.items()}
        self._run_button.setEnabled(False)
        self._header.set_status("Reviewing: positions, history, risk, gates, valuations…")
        self._tasks.submit(lambda: self._review.run(ticks), on_done=self._on_review,
                           on_error=self._on_run_error)

    def _on_run_error(self, exc: Exception) -> None:
        self._run_button.setEnabled(True)
        self._error_note.set_text(str(exc))
        self._error_note.setVisible(True)
        self._header.set_status("The review stopped: see the rules panel.", error=True)
        if not isinstance(exc, (ReviewError, ValueError)):
            _logger.error("review: %s", exc)

    def _on_review(self, review: Review) -> None:
        self._run_button.setEnabled(True)
        self._last = review
        self._error_note.setVisible(False)
        self._on_rules((review.rules, review.version_id, False))
        fails = review.breaches(Grade.FAIL)
        warns = review.breaches(Grade.WARN)
        gates = [c for c in review.breaches() if isinstance(c.check.consequence, Effect)
                 and c.check.consequence is not Effect.NOTE]
        self._tiles["fail"].set_value(str(len(fails)), "critical" if fails else "good")
        self._tiles["warn"].set_value(str(len(warns)))
        self._tiles["gates"].set_value(str(len(gates)), "critical" if gates else "good")
        self._render_checklist(review)
        self._render_streams(review)
        self._render_market(review)
        self._render_influence(review)
        self._render_drafts(review)
        self._notes.setPlainText("")
        notes = list(review.notes) + [f"Skipped: {s}" for s in review.skipped]
        self._run_notes.set_text(" ".join(notes))
        self._run_notes.setVisible(bool(notes))
        source = ", ".join(review.files) or review.snapshot.name or "the newest snapshot"
        self._header.set_status(f"Review #{review.review_id}: {len(fails)} fails, {len(warns)} "
                                f"warns, {len(gates)} gates closed; positions as of "
                                f"{review.snapshot.as_of:%Y-%m-%d} from {source}.")
        self._refresh_journal()

    def _render_checklist(self, review: Review) -> None:
        rows = sorted(review.checks, key=lambda c: (_severity(c.check), c.check.rule.key, c.item))
        table = [[f"{_name(c.check.rule)} {_limit(c.check)}", _item(c.item), _value(c.check),
                  _result(c.check)]
                 for c in rows if not c.check.rule.key.startswith("7.")]
        self._checklist.set_rows(table)
        self._checklist.setFixedHeight(_HEADER + max(len(table), 1) * _ROW)

    def _render_streams(self, review: Review) -> None:
        rows = [[v.sleeve, ", ".join(v.breaches) or ("none", PALETTE.muted), str(v.run),
                 (v.grade.value, PALETTE.critical if v.grade is Grade.FAIL else PALETTE.warning)
                 if v.grade else ("holds", PALETTE.good)] for v in review.streams]
        self._streams.set_rows(rows or [[("No stream sleeves in section 3", PALETTE.muted),
                                         "", "", ""]])
        self._streams.setFixedHeight(_HEADER + max(len(rows), 1) * _ROW)

    def _render_market(self, review: Review) -> None:
        if review.calm is not None and review.graded.returns is not None:
            dates = review.graded.returns.index
            rule = review.rules.rule("7.calm")
            references = [("gate", rule.limit.lower)] if rule and rule.limit.lower else []
            self._calm_chart.set_panels(dates, [Panel(
                "Calm-regime probability, filtered", [Series("Calm", review.calm.history,
                                                             PALETTE.series, fmt="{:.0%}")],
                axis="number", references=references, y_range=(0.0, 1.0))])
        rows = [[_name(c.check.rule), _value(c.check), _limit(c.check), _result(c.check)]
                for c in review.checks if c.check.rule.key.startswith("7.")]
        self._market.set_rows(rows or [[("No gates on in section 7", PALETTE.muted), "", "", ""]])
        self._market.setFixedHeight(_HEADER + max(len(rows), 1) * _ROW)

    def _render_influence(self, review: Review) -> None:
        # One row per series a day decides: the mix's, then each holding's own
        # (models/portfolio.py, D53). A robust share beyond 50% is the flag.
        rows = []
        for d in review.influential:
            when = f"{d.date:%Y-%m-%d}"
            if d.decides_mix:
                rows.append([when, "the mix", f"{d.mix_return:+.1%}", f"{d.variance_share:.1%}",
                             (f"{d.robust_share:.1%}", PALETTE.critical)])
            rows.extend([when, h.symbol, f"{h.holding_return:+.1%}", f"{h.variance_share:.1%}",
                         (f"{h.robust_share:.1%}", PALETTE.critical)] for h in d.holdings)
        self._influence_card.set_subtitle(
            ("Graded without these days (section 4)" if review.graded is not review.view
             else "Graded as measured (section 4)") if rows
            else "No day outweighs the rest of the mix or of a holding")
        self._influence.set_rows(rows)
        self._influence.setFixedHeight(_HEADER + max(len(rows), 1) * _ROW)

    def _render_drafts(self, review: Review) -> None:
        rows = [[d.symbol, f"{d.before:g}", f"{d.after:g}",
                 (f"{d.change:+g}", PALETTE.good if d.change > 0 else PALETTE.critical),
                 f"${d.price:,.2f}" if d.price else "—"] for d in review.drafts]
        self._drafts.set_rows(rows)
        self._drafts.setFixedHeight(_HEADER + max(len(rows), 1) * _ROW)

    # ------------------------------------------------------------- journal
    def _draft_to_form(self, row: int) -> None:
        if self._last is None or not 0 <= row < len(self._last.drafts):
            return
        draft = self._last.drafts[row]
        self._symbol.set_text(draft.symbol)
        self._quantity.set_text(f"{draft.change:g}")
        self._trade_price.set_text(f"{draft.price:.2f}" if draft.price else "")

    def _record_trade(self) -> None:
        try:
            symbol = self._symbol.required_text()
            quantity = self._quantity.number()
            price_text = self._trade_price.text().strip()
            price = self._trade_price.number() if price_text else None
        except ValueError as exc:
            self._header.set_status(str(exc), error=True)
            return
        rule = self._trade_rule.current()
        review_id = self._last.review_id if self._last else None
        reason = self._reason.text()
        self._tasks.submit(lambda: self._review.add_trade(
            review_id, symbol, quantity, price, reason, "" if rule == _NO_RULE else rule),
            on_done=self._on_trade, on_error=lambda e: self._header.set_status(str(e), error=True))

    def _on_trade(self, trade_id: int) -> None:
        self._header.set_status(f"Recorded trade #{trade_id}.")
        for field in (self._symbol, self._quantity, self._trade_price, self._reason):
            field.set_text("")
        self._refresh_journal()

    def _save_notes(self) -> None:
        if self._last is None:
            self._header.set_status("Run a review first.", error=True)
            return
        review_id, text = self._last.review_id, self._notes.toPlainText()
        self._tasks.submit(lambda: self._review.save_notes(review_id, text),
                           on_done=lambda _r: self._header.set_status("Notes saved."),
                           on_error=lambda e: self._header.set_status(str(e), error=True))

    def _refresh_journal(self) -> None:
        self._tasks.submit(lambda: (self._review.reviews(20), self._review.trades()),
                           on_done=self._render_journal,
                           on_error=lambda e: _logger.error("review journal: %s", e))

    def _render_journal(self, result) -> None:
        reviews, trades = result
        rows = []
        for r in reviews:
            checks = r.payload.get("checks", [])
            fails = sum(c.get("consequence") == Grade.FAIL.value for c in checks)
            warns = sum(c.get("consequence") == Grade.WARN.value for c in checks)
            rows.append([f"{r.run_at:%Y-%m-%d %H:%M}", r.payload.get("rules", {}).get("version", ""),
                         r.snapshot_as_of, (str(fails), PALETTE.critical if fails else PALETTE.good),
                         str(warns)])
        self._history.set_rows(rows)
        self._history.setFixedHeight(_HEADER + max(len(rows), 1) * _ROW)
        trade_rows = [[f"{t.written_at:%Y-%m-%d %H:%M}", t.symbol, f"{t.quantity:+g}",
                       f"${t.price:,.2f}" if t.price else "—", t.rule or "—", t.reason]
                      for t in trades]
        self._trades.set_rows(trade_rows)
        self._trades.setFixedHeight(_HEADER + max(len(trade_rows), 1) * _ROW)

    def _clear(self) -> None:
        for key in ("fail", "warn", "gates"):
            self._tiles[key].set_value("—")
        self._tiles["rules"].set_value("—")
        for table in (self._checklist, self._streams, self._market, self._influence,
                      self._drafts, self._trades, self._history):
            table.setFixedHeight(_HEADER + _ROW)
        self._influence_card.set_subtitle("Run a review to see it")
        self._header.set_status("Run a review: it reads your rules and the newest export.")
