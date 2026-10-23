r"""``REGIME.md`` → a ``RuleSet`` (W7).

The owner's file is Markdown:
- a header line: ``Version: … · Written: … · Reason for this version: …``
- ten numbered sections (``## 4. Portfolio limits``)
- tables whose first column names each row

This module reads it the way the template promises ("How W7 reads a
value"):
- A blank (``___``) leaves its rule off. One side of a range can stay
  blank.
- Percent rows take percent numbers: ``15`` means 15%.
- ``by k SE`` in a value is the rule's evidence k (``models/regime.py``).
- Rows are recognized by their first column (``_RULES``). An unknown label
  in a rule table is an error, not a skipped row, so a renamed row can't
  silently turn its rule off.

Every value is read or refused. A value that can't be read, or a filled-in
value whose "On breach" is blank, raises ``RegimeFileError`` naming its
line.

Which measure each rule reads, and over what (each holding, each stream,
the portfolio), is the review's business (``services/review.py``). This
module only names the measure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from quant_hub.data.regime import (
    Effect,
    Grade,
    Limit,
    MarketSettings,
    Rule,
    RuleSet,
    Settings,
    SleeveKind,
    SleeveRow,
    Universe,
)


class RegimeFileError(ValueError):
    """The file can't be read as a regime; the message names the line."""

    def __init__(self, line: int, message: str) -> None:
        super().__init__(f"REGIME.md line {line}: {message}")
        self.line = line


BLANK = "___"
_HEADING = re.compile(r"^##\s+(\d+)\.\s+(.*)$")
_HEADER = re.compile(r"Version:\s*(?P<version>.*?)\s*·\s*Written:\s*(?P<written>.*?)\s*·\s*"
                     r"Reason for this version:\s*(?P<reason>.*)$")
_EVIDENCE = re.compile(r"\bby\s+(\S+)\s+SE\b", re.IGNORECASE)
# A sign counts only where a number starts, so "5-10" is a range, not 5 and -10.
_NUMBER = r"(?<![\d.])[-−]?\$?\d[\d,]*(?:\.\d+)?"
_TOKEN = re.compile(rf"{BLANK}|{_NUMBER}")
_CELL_SPLIT = re.compile(r"(?<!\\)\|")
_RUNS = re.compile(r"failing\s+(\S+)\s+reviews in a row", re.IGNORECASE)
_SLEEVE_GRADE = re.compile(r"^Sleeve size outside its intended range:\s*(.*)$")
_CHANGE = re.compile(r"before a change[^:]*:\s*(.*)$", re.IGNORECASE)
_DOMINANT = re.compile(r"grade the return-based numbers:\s*(.*?)\s*\(as measured")
_K = re.compile(r"\bK\s*=\s*(\S+)")
_OVER_DAYS = re.compile(r"\bover\s+(\S+)\s+days\b")
_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

# How a row's value reads (``_Spec.kind``).
UPPER, LOWER, RANGE, HURDLE, SHORT_OPTIONS, ODDS, INDICATORS = (
    "upper", "lower", "range", "hurdle", "short options", "odds", "indicators")
# What its numbers are (``_Spec.scale``).
PERCENT, NUMBER, COUNT, DOLLARS = "percent", "number", "count", "dollars"


@dataclass(frozen=True)
class _Spec:
    key: str
    measure: str
    kind: str
    scale: str = NUMBER
    unit: str = ""
    gate: bool = False      # section 7: the last column is an effect, not a grade


_RULES: dict[str, _Spec] = {
    # 4. Portfolio limits
    "Max single holding weight": _Spec("4.max-weight", "holding weight", UPPER, PERCENT, "% of equity"),
    "Max single holding risk share": _Spec("4.max-risk-share", "holding risk share", UPPER,
                                           PERCENT, "% of portfolio vol"),
    "Portfolio volatility band": _Spec("4.volatility", "portfolio volatility", RANGE, PERCENT,
                                       "% a year"),
    "Beta band": _Spec("4.beta", "portfolio beta", RANGE, NUMBER, "vs the benchmark"),
    "1-day VaR limit (95%)": _Spec("4.var", "var 95", UPPER, PERCENT, "% of equity"),
    "1-day ES limit (99%)": _Spec("4.es", "es 99", UPPER, PERCENT, "% of equity"),
    "Drawdown tolerance": _Spec("4.drawdown", "worst drawdown", UPPER, PERCENT, "%"),
    "Cash range": _Spec("4.cash", "cash", RANGE, PERCENT, "% of equity"),
    "Minimum effective holdings": _Spec("4.effective-holdings", "effective holdings", LOWER,
                                        NUMBER, "count"),
    "Sizing cap": _Spec("4.sizing", "kelly fraction", UPPER, NUMBER, "fraction of Kelly"),
    # 5. Hedges and options
    "Hedge budget": _Spec("5.hedge-budget", "hedge cost", UPPER, PERCENT, "% of equity a year"),
    "Max premium at risk": _Spec("5.premium", "premium at risk", UPPER, PERCENT, "% of equity"),
    "Net delta band": _Spec("5.net-delta", "net delta", RANGE, PERCENT, "% of equity"),
    "Vega limit": _Spec("5.vega", "book vega", UPPER, DOLLARS, "$ per vol point"),
    "Short options allowed": _Spec("5.short-options", "short options", SHORT_OPTIONS, COUNT,
                                   "positions"),
    "Minimum days to expiry held": _Spec("5.expiry", "days to expiry", LOWER, COUNT,
                                         "calendar days"),
    # 6. The thesis, checked
    "A stream must clear its growth hurdle": _Spec("6.hurdle", "stream margin", HURDLE, NUMBER,
                                                   "a year"),
    "Max correlation between streams": _Spec("6.correlation", "stream residual correlation",
                                             UPPER, NUMBER, "|ρ|"),
    'Max beta of a "stream" to the core': _Spec("6.stream-beta", "stream beta", UPPER, NUMBER,
                                                "β"),
    "Fractional sizing": _Spec("6.fraction", "sleeve fraction of optimum", UPPER, NUMBER, "f"),
    # 7. Market-state gates
    "Calm-regime probability": _Spec("7.calm", "calm probability", LOWER, PERCENT, "%", True),
    "Volatility forecast": _Spec("7.volatility", "volatility forecast", UPPER, PERCENT,
                                 "% a year", True),
    "Regime odds ahead": _Spec("7.odds", "calm odds", ODDS, PERCENT, "%", True),
    "Your own indicators": _Spec("7.indicator", "indicator", INDICATORS, COUNT, "", True),
    # 8. Universe and screens
    "DCF margin of safety to add a name": _Spec("8.margin", "margin of safety", LOWER, PERCENT,
                                                "%"),
    "Max terminal share of EV": _Spec("8.terminal", "terminal share", UPPER, PERCENT, "% of EV"),
    "Implied growth vs history": _Spec("8.implied", "implied growth", UPPER, NUMBER,
                                       "× trailing revenue CAGR"),
    "Missing filing fields allowed": _Spec("8.missing", "missing fields", UPPER, COUNT,
                                           "fields"),
}
_TEXT_ROWS = {"Universe", "Excluded kinds", "Valuation refresh"}
_SETTINGS = {
    "Accounts in scope": "accounts",
    "Positions refresh": "refresh",
    "History source": "history_source",
    "Benchmark (the core's yardstick)": "benchmark",
    "Cash rate": "cash_rate",
    "Lookback for risk numbers": "lookback_days",
    "Regression window": "regression_window",
}
_MIN_LOOKBACK = 120
_HURDLE_WORDS = {"above the hurdle", "estimate above the hurdle", "yes", "on"}
_SHORT_OPTIONS = {"yes", "covered only", "no"}


def read_regime(path: str | Path) -> RuleSet:
    """Read the owner's file (module docstring)."""
    return parse_regime(Path(path).read_text(encoding="utf-8"))


def parse_regime(text: str) -> RuleSet:
    """``text`` → a RuleSet, or ``RegimeFileError`` naming the line."""
    lines = text.splitlines()
    sections: dict[int, list[tuple[int, str]]] = {}
    current = 0
    header = None
    for number, line in enumerate(lines, start=1):
        heading = _HEADING.match(line.strip())
        if heading:
            current = int(heading.group(1))
            sections[current] = []
            continue
        if current == 0 and header is None and (m := _HEADER.search(line)):
            header = (number, m)
        sections.setdefault(current, []).append((number, line))
    if header is None:
        raise RegimeFileError(1, "no 'Version: … · Written: … · Reason for this version: …' line")
    line, m = header
    version, written, reason = (_text(m.group(g)) for g in ("version", "written", "reason"))
    return RuleSet(
        version=version or "",
        written=_date(line, written) if written else None,
        reason=reason or "",
        thesis_runs=_thesis_runs(sections.get(1, [])),
        settings=_settings(sections.get(2, [])),
        sleeves=_sleeves(sections.get(3, [])),
        sleeve_consequence=_sleeve_grade(sections.get(3, [])),
        rules=_rule_rows([*sections.get(4, []), *sections.get(5, []), *sections.get(6, []),
                          *sections.get(7, []), *sections.get(8, [])]),
        market=_market(sections.get(7, [])),
        universe=_universe(sections.get(8, [])),
        routine=tuple((when, _text(steps) or "", _text(kept) or "")
                      for _, (when, steps, kept, *_) in _rows(sections.get(9, []), minimum=3)),
        change_policy=_change_policy(sections.get(10, [])),
        without_dominant_day=_dominant_day(sections.get(4, [])),
    )


# ------------------------------------------------------------------ tables

def _rows(lines: list[tuple[int, str]], minimum: int = 2) -> list[tuple[int, list[str]]]:
    """Each table's data rows as (line, cells), without header and rule rows.
    Escaped pipes (``\\|``) stay inside their cell."""
    rows, header_next = [], True
    for number, line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            header_next = True
            continue
        cells = [cell.strip().replace("\\|", "|") for cell in _CELL_SPLIT.split(stripped)[1:-1]]
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        if header_next:
            header_next = False
            continue
        if len(cells) < minimum:
            raise RegimeFileError(number, f"a table row needs {minimum} columns, not {len(cells)}")
        rows.append((number, cells))
    return rows


def _text(cell: str) -> str | None:
    """A text cell, or None when it's blank (it starts with ``___``)."""
    cell = cell.strip().strip("`").strip()
    return None if not cell or cell.startswith(BLANK) or cell in {"—", "-"} else cell


def _tokens(line: int, cell: str) -> tuple[list[float | None], float]:
    """The numbers in a value (None for a blank) and its evidence k."""
    k = 0.0
    evidence = _EVIDENCE.search(cell)
    if evidence:
        k = _number(line, evidence.group(1))
        if k < 0:
            raise RegimeFileError(line, f"evidence k must not be negative, not {k}")
        cell = cell[:evidence.start()] + cell[evidence.end():]
    return [None if token == BLANK else _number(line, token) for token in _TOKEN.findall(cell)], k


def _number(line: int, token: str) -> float:
    try:
        return float(token.replace("−", "-").replace("$", "").replace(",", ""))
    except ValueError:
        raise RegimeFileError(line, f"{token!r} isn't a number") from None


def _scaled(value: float | None, scale: str) -> float | None:
    if value is None:
        return None
    return value / 100.0 if scale == PERCENT else value


def _consequence(line: int, cell: str, gate: bool) -> Grade | Effect | None:
    text = _text(cell)
    if text is None:
        return None
    word = text.lower()
    kinds = Effect if gate else Grade
    for member in kinds:
        if word == member.value:
            return member
    allowed = " / ".join(member.value for member in kinds)
    raise RegimeFileError(line, f"{text!r} isn't one of {allowed}")


# ------------------------------------------------------------------ sections

def _thesis_runs(lines: list[tuple[int, str]]) -> int | None:
    for number, line in lines:
        m = _RUNS.search(line)
        if m:
            if m.group(1) == BLANK:
                return None
            runs = _number(number, m.group(1))
            if runs < 1 or runs != int(runs):
                raise RegimeFileError(number, f"N reviews in a row must be a whole number ≥ 1, "
                                              f"not {m.group(1)}")
            return int(runs)
    return None


def _settings(lines: list[tuple[int, str]]) -> Settings:
    values: dict[str, object] = {}
    for number, (label, value, *_) in _rows(lines):
        field = _SETTINGS.get(label)
        if field is None:
            raise RegimeFileError(number, f"unknown setting {label!r}")
        text = _text(value)
        if text is None:
            continue
        if field == "accounts":
            values[field] = tuple(a.strip() for a in text.split(",") if a.strip())
        elif field == "benchmark":
            ticker = text.upper()
            if not _TICKER.match(ticker):
                raise RegimeFileError(number, f"{text!r} isn't a ticker")
            values[field] = ticker
        elif field == "history_source":
            values[field] = text.lower()
        elif field == "cash_rate":
            m = re.search(rf"({_NUMBER})\s*%", text)
            if not m:
                raise RegimeFileError(number, f"the cash rate needs a percent, e.g. 4.25 %: {text!r}")
            values[field] = _number(number, m.group(1)) / 100.0
        elif field == "lookback_days":
            numbers, _ = _tokens(number, text)
            if len(numbers) != 1 or numbers[0] is None or numbers[0] != int(numbers[0]):
                raise RegimeFileError(number, f"the lookback is a whole number of days: {text!r}")
            if numbers[0] < _MIN_LOOKBACK:
                raise RegimeFileError(number, f"the lookback must be at least {_MIN_LOOKBACK} "
                                              f"days, not {numbers[0]:g}")
            values[field] = int(numbers[0])
        else:
            values[field] = text
    return Settings(**values)


def _sleeves(lines: list[tuple[int, str]]) -> tuple[SleeveRow, ...]:
    sleeves: list[SleeveRow] = []
    for number, (symbol, sleeve, why, intended, *_) in _rows(lines, minimum=4):
        ticker = _text(symbol)
        if ticker is None:
            continue
        if any(row.symbol == ticker.upper() for row in sleeves):
            raise RegimeFileError(number, f"{ticker} is already in a sleeve")
        kind, name = _sleeve(number, sleeve)
        limit, target = None, None
        values, _ = _tokens(number, intended)
        filled = [v for v in values if v is not None]
        if len(values) == 2 and filled:
            limit = _limit(number, _scaled(values[0], PERCENT), _scaled(values[1], PERCENT))
        elif len(values) == 1 and filled:
            target = _scaled(values[0], PERCENT)
        elif len(values) > 2:
            raise RegimeFileError(number, f"an intended size is a number or a range: {intended!r}")
        sleeves.append(SleeveRow(ticker.upper(), kind, name, _text(why) or "", limit, target))
    return tuple(sleeves)


def _sleeve(line: int, cell: str) -> tuple[SleeveKind | None, str]:
    text = _text(cell)
    if text is None:
        return None, ""
    kind_text, _, name = (part.strip() for part in text.partition(":"))
    try:
        kind = SleeveKind(kind_text.lower())
    except ValueError:
        raise RegimeFileError(line, f"{text!r} isn't core, hedge, other or stream: <name>") from None
    if kind is SleeveKind.STREAM and not name:
        raise RegimeFileError(line, "each stream needs its own name: stream: <name>")
    if kind is not SleeveKind.STREAM and name:
        raise RegimeFileError(line, f"only a stream takes a name, not {kind.value}")
    return kind, name if kind is SleeveKind.STREAM else kind.value


def _sleeve_grade(lines: list[tuple[int, str]]) -> Grade | None:
    for number, line in lines:
        m = _SLEEVE_GRADE.match(line.strip())
        if m:
            word = m.group(1).split("(")[0].strip()
            return _consequence(number, word, gate=False) if word else None
    return None


def _rule_rows(lines: list[tuple[int, str]]) -> tuple[Rule, ...]:
    rules: list[Rule] = []
    seen: dict[str, int] = {}
    for number, cells in _rows(lines, minimum=3):
        label = cells[0]
        if label in _TEXT_ROWS:
            continue
        spec = _RULES.get(label)
        if spec is None:
            raise RegimeFileError(number, f"unknown rule {label!r} (rows are recognized by "
                                          "their first column)")
        if label in seen:
            raise RegimeFileError(number, f"{label!r} is already on line {seen[label]}")
        seen[label] = number
        rules.extend(_rule(number, spec, label, cells))
    return tuple(rules)


def _rule(line: int, spec: _Spec, label: str, cells: list[str]) -> list[Rule]:
    """The rules one row makes: none when it's blank, one usually, one per
    indicator for the owner's own."""
    consequence = _consequence(line, cells[-1], spec.gate)
    rules = _limits(line, spec, label, cells[1])
    if rules and consequence is None:
        word = "effect" if spec.gate else "'On breach' grade"
        raise RegimeFileError(line, f"{label}: a value without an {word}")
    return [Rule(key, name, measure, limit, consequence, k, spec.unit)
            for key, name, measure, limit, k in rules]


def _limits(line: int, spec: _Spec, label: str, value: str
            ) -> list[tuple[str, str, str, Limit, float]]:
    """(key, label, measure, limit, k) for each rule a row's value makes."""
    one = (spec.key, label, spec.measure)
    if spec.kind == INDICATORS:
        text = _text(value)
        names = [n.strip() for n in (text or "").split(";") if n.strip()]
        return [(f"{spec.key}.{i}", name, f"{spec.measure}: {name}", Limit(lower=1.0), 0.0)
                for i, name in enumerate(names, start=1)]
    if spec.kind == SHORT_OPTIONS:
        text = _text(value)
        if text is None or text.lower() == "yes":
            return []
        if text.lower() not in _SHORT_OPTIONS:
            raise RegimeFileError(line, f"{text!r} isn't yes, covered only or no")
        measure = "uncovered short options" if text.lower() == "covered only" else spec.measure
        return [(spec.key, label, measure, Limit(upper=0.0), 0.0)]
    if spec.kind == HURDLE:
        text = _text(value)
        if text is None:
            return []
        numbers, k = _tokens(line, text)
        if not _EVIDENCE.search(text) and text.lower() not in _HURDLE_WORDS:
            raise RegimeFileError(line, f"{text!r}: write 'above the hurdle' or 'by k SE'")
        if numbers:
            raise RegimeFileError(line, f"the hurdle is zero; only 'by k SE' takes a number: "
                                        f"{text!r}")
        return [(*one, Limit(lower=0.0), k)]

    numbers, k = _tokens(line, value)
    if spec.kind == ODDS:
        if len(numbers) != 2:
            raise RegimeFileError(line, f"write '≥ p % calm in h days': {value!r}")
        probability, days = numbers
        if probability is None:
            return []
        if days is None:
            raise RegimeFileError(line, "the odds need their horizon in days")
        return [(*one, _limit(line, _scaled(probability, PERCENT), None), k)]
    if spec.kind == RANGE:
        if len(numbers) != 2:
            raise RegimeFileError(line, f"a range is 'low – high' (either may be ___): {value!r}")
        lower, upper = (_scaled(n, spec.scale) for n in numbers)
        if lower is None and upper is None:
            return []
        return [(*one, _limit(line, lower, upper), k)]
    if len(numbers) != 1:
        raise RegimeFileError(line, f"one number expected: {value!r}")
    if numbers[0] is None:
        return []
    bound = _scaled(numbers[0], spec.scale)
    if spec.scale == COUNT and (bound < 0 or bound != int(bound)):
        raise RegimeFileError(line, f"a count is a whole number ≥ 0, not {numbers[0]:g}")
    if spec.key == "6.correlation" and not 0.0 <= bound < 1.0:
        raise RegimeFileError(line, f"a limit on |ρ| must be in [0, 1), not {bound:g}")
    limit = _limit(line, None, bound) if spec.kind == UPPER else _limit(line, bound, None)
    return [(*one, limit, k)]


def _limit(line: int, lower: float | None, upper: float | None) -> Limit:
    try:
        return Limit(lower, upper)
    except ValueError as exc:
        raise RegimeFileError(line, str(exc)) from None


def _market(lines: list[tuple[int, str]]) -> MarketSettings:
    regimes = volatility_horizon = odds_horizon = None
    indicators: tuple[str, ...] = ()
    for number, cells in _rows(lines, minimum=3):
        label, value, measured = cells[0], cells[1], cells[2]
        if label == "Calm-regime probability":
            m = _K.search(measured)
            if m and m.group(1).strip(" (") != BLANK:
                regimes = _whole(number, m.group(1).strip(" ("), "K", minimum=2)
        elif label == "Volatility forecast":
            m = _OVER_DAYS.search(measured)
            if m and m.group(1) != BLANK:
                volatility_horizon = _whole(number, m.group(1), "the horizon", minimum=1)
        elif label == "Regime odds ahead":
            numbers, _ = _tokens(number, value)
            if len(numbers) == 2 and numbers[1] is not None:
                odds_horizon = _whole(number, f"{numbers[1]:g}", "the horizon", minimum=1)
        elif label == "Your own indicators":
            text = _text(value)
            indicators = tuple(n.strip() for n in (text or "").split(";") if n.strip())
    return MarketSettings(regimes, volatility_horizon, odds_horizon, indicators)


def _whole(line: int, token: str, what: str, minimum: int) -> int:
    value = _number(line, token)
    if value != int(value) or value < minimum:
        raise RegimeFileError(line, f"{what} must be a whole number ≥ {minimum}, not {token}")
    return int(value)


def _universe(lines: list[tuple[int, str]]) -> Universe:
    values = {"Universe": "", "Excluded kinds": "", "Valuation refresh": ""}
    for number, cells in _rows(lines, minimum=3):
        if cells[0] in values:
            values[cells[0]] = _text(cells[1]) or ""
    tickers = tuple(t.upper() for t in re.split(r"[,\s]+", values["Universe"]) if t)
    for ticker in tickers:
        if not _TICKER.match(ticker):
            raise RegimeFileError(lines[0][0] if lines else 1, f"{ticker!r} isn't a ticker")
    return Universe(tickers, values["Excluded kinds"], values["Valuation refresh"])


def _dominant_day(lines: list[tuple[int, str]]) -> bool:
    """Section 4's choice for a flagged holding: grade without that day?
    The sentence wraps, so it's read from the joined paragraph."""
    text = " ".join(line.strip() for _, line in lines if not line.strip().startswith("|"))
    m = _DOMINANT.search(text)
    if not m:
        return False
    choice = m.group(1).strip().lower()
    if choice in {BLANK, "", "as measured"}:
        return False
    if choice == "without that day":
        return True
    line = next((n for n, l in lines if "grade the return-based numbers" in l), lines[0][0])
    raise RegimeFileError(line, f"{m.group(1).strip()!r} isn't 'as measured' or "
                                "'without that day'")


def _change_policy(lines: list[tuple[int, str]]) -> str:
    for _, line in lines:
        m = _CHANGE.search(line)
        if m:
            return _text(m.group(1)) or ""
    return ""


def _date(line: int, text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise RegimeFileError(line, f"'Written' is a date like 2026-10-22, not {text!r}") from None
