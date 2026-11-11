"""The literature timeline tab: the canon over time, and one paper up close (Lit-2).

The chart (``gui/timeline.py``) draws; this tab turns the service's merged
papers and the canon's links into chart records, and shows the selected
paper in the inspector: its question, what it changed and assumed, its
links both ways (each one a click away), the modules that implement it, and
your reading status, PDF and source. Disk and catalog work run on the
TaskRunner, as on the Papers tab.
"""
from datetime import date
from html import escape

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QScrollArea, QVBoxLayout,
                               QWidget)

from quant_hub.data.literature import CRITIQUES, INCOMING, LINK_KINDS
from quant_hub.gui.components import (Badge, Card, EmptyState, Field, PageStack, SelectField,
                                      button, divider, form_grid, label, set_role)
from quant_hub.gui.theme import FIELD_L, PALETTE, SIDE_PANEL_WIDTH, SPACE_L, SPACE_M, SPACE_S
from quant_hub.gui.timeline import Band, Edge, Node, TimelineChart, spread

_STATUSES = ["To read", "Reading", "Read", "Replicated"]
_SCROLLBAR = 12
_INNER = SIDE_PANEL_WIDTH - 2 * SPACE_L - _SCROLLBAR
_HALF = (_INNER - SPACE_M) // 2
_VERIFIED = {
    "crossref": "Metadata checked against Crossref: title, first author and year",
    "arxiv": "Metadata checked against arXiv: title, first author and year",
}


def _fraction_of_year(day: date) -> float:
    return day.year + (day.timetuple().tm_yday - 1) / 365.25


def _link(paper_id: str, text: str) -> str:
    return (f"<a href='{paper_id}' style='color:{PALETTE.series_light}; "
            f"text-decoration:none'>{escape(text)}</a>")


class LiteratureTimeline(QWidget):
    def __init__(self, service, tasks, status):
        super().__init__()
        self._service, self._tasks, self._status = service, tasks, status
        self._canon = service.canon
        self._papers: dict[str, dict] = {}
        self._busy = False
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SPACE_L)
        count = len(self._canon.papers)
        first = min((p.when for p in self._canon.papers), default=0)
        last = max((p.when for p in self._canon.papers), default=0)
        chart_card = Card("Literature timeline",
                          f"{count} papers, {first}–{last}, by strand · their links appear when you pick one")
        self._search = Field("Find", width=FIELD_L, placeholder="author, idea or year…", inline=True)
        self._search.edit.textChanged.connect(self._find)
        chart_card.add_action(self._search)
        chart_card.add_action(button("Show all", icon_name="ph.arrows-out-line-horizontal",
                                     on_click=lambda: self._chart.fit()))
        self._chart_stack = PageStack()
        self._chart = TimelineChart()
        self._chart.setMinimumHeight(700)
        self._chart.selected.connect(self._show)
        self._chart.opened.connect(self._open)
        self._chart_stack.addWidget(self._chart)
        self._broken = EmptyState("ph.warning", "The literature canon didn't load",
                                  service.canon_error or "")
        self._chart_stack.addWidget(self._broken)
        if service.canon_error:
            self._chart_stack.setCurrentWidget(self._broken)
        chart_card.body.addWidget(self._chart_stack, 1)
        outer.addWidget(chart_card, 1)
        inspector = Card("Paper", "Its question, its links, and where it lives in your code")
        inspector.setFixedWidth(SIDE_PANEL_WIDTH)
        scroll = QScrollArea()
        set_role(scroll, "cardScroll")
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("cardScrollBody")
        self._details = QVBoxLayout(body)
        self._details.setContentsMargins(0, 0, _SCROLLBAR, 0)
        self._details.setSpacing(SPACE_S)
        scroll.setWidget(body)
        inspector.body.addWidget(scroll, 1)
        outer.addWidget(inspector)
        self._empty()

    # -- loading ------------------------------------------------------------
    def refresh(self):
        self._tasks.submit(self._service.papers, on_done=self._loaded, on_error=self._error)

    def _loaded(self, papers):
        self._papers = {p["id"]: p for p in papers}
        canon = self._canon
        lanes = {s.id: i for i, s in enumerate(canon.strands)}
        cited = {p.id: 0 for p in canon.papers}
        for p in canon.papers:
            for link in p.links:
                cited[link.to] += 1
        years = spread([(lanes[p.strand], p.when) for p in canon.papers])
        nodes = []
        for paper, year in zip(canon.papers, years):
            mine = self._papers.get(paper.id, {})
            readout = (f"<b style='color:{PALETTE.text}'>{escape(paper.label)}</b>&nbsp;&nbsp;"
                       f"{escape(paper.title)}&nbsp;&nbsp;·&nbsp;&nbsp;{escape(paper.venue)}")
            nodes.append(Node(paper.id, lanes[paper.strand], year, paper.label, readout,
                              paper.implemented, mine.get("status") in ("Read", "Replicated"),
                              cited[paper.id] + 0.5 * paper.implemented))
        edges = [Edge(p.id, link.to, link.kind in CRITIQUES, link.kind == "independent")
                 for p in canon.papers for link in p.links]
        bands = [Band(e.start, e.end, e.name, e.long) for e in canon.eras]
        current = self._chart.current()
        self._chart.set_timeline([s.name for s in canon.strands], nodes, edges, bands,
                                 _fraction_of_year(date.today()))
        if current:
            self._chart.select(current, reveal=False)
            self._show(current)
        self._find(self._search.edit.text())

    def _error(self, exc):
        self._busy = False
        self._status(str(exc), error=True)

    # -- search -------------------------------------------------------------
    def _find(self, text: str):
        query = text.casefold().strip()
        if not query:
            self._chart.set_lit(None)
            return
        names = {s.id: s.name for s in self._canon.strands}
        hits = set()
        for p in self._canon.papers:
            haystack = " ".join([p.label, p.title, " ".join(p.authors), p.venue, names[p.strand],
                                 p.question, p.changed, p.assumed, str(p.year)]).casefold()
            if query in haystack:
                hits.add(p.id)
        self._chart.set_lit(hits)
        self._status(f"{len(hits)} paper{'s' if len(hits) != 1 else ''} match “{text.strip()}”")

    # -- the inspector ------------------------------------------------------
    def _clear(self):
        # Hide before deleteLater: deletion waits for the event loop, and the
        # old widgets would show under the new ones until then.
        def drop(widget):
            widget.hide()
            widget.deleteLater()
        while self._details.count():
            item = self._details.takeAt(0)
            if item.widget() is not None:
                drop(item.widget())
            elif item.layout() is not None:
                while item.layout().count():
                    child = item.layout().takeAt(0)
                    if child.widget() is not None:
                        drop(child.widget())

    def _empty(self):
        self._clear()
        self._details.addWidget(EmptyState("ph.books", "Pick a paper",
            "Click a dot or a label. Scroll to zoom into a decade, drag to move through time."))
        self._details.addStretch(1)

    def _text(self, text: str, role: str | None = None, rich: bool = False):
        """A wrapped label at the inspector's known width, tall enough for its
        text: the page body ignores height-for-width (D48, D49)."""
        widget = label(text, role, wrap=True)
        widget.setTextFormat(Qt.RichText if rich else Qt.PlainText)
        widget.setFixedWidth(_INNER)
        widget.ensurePolished()
        widget.setMinimumHeight(widget.heightForWidth(_INNER))
        self._details.addWidget(widget)
        return widget

    def _section(self, title: str, text: str):
        if text:
            self._details.addSpacing(SPACE_S)
            self._details.addWidget(label(title.upper(), "section"))
            self._text(text)

    def _links(self, title: str, rows: list[tuple[str, str, str]]):
        """``rows``: (verb, paper id, label), each a click away."""
        if not rows:
            return
        self._details.addSpacing(SPACE_S)
        self._details.addWidget(label(title.upper(), "section"))
        muted = f"color:{PALETTE.muted}"
        html = "<br>".join(f"<span style='{muted}'>{escape(verb)}</span>&nbsp; {_link(pid, text)}"
                           for verb, pid, text in rows)
        links = self._text(html, rich=True)
        links.setTextInteractionFlags(Qt.LinksAccessibleByMouse)
        links.linkActivated.connect(self._go)

    def _go(self, paper_id: str):
        self._chart.select(paper_id)
        self._show(paper_id)

    def _show(self, paper_id: str):
        if paper_id not in self._canon:
            return
        paper = self._canon.paper(paper_id)
        mine = self._papers.get(paper_id, {})
        strand = next(s.name for s in self._canon.strands if s.id == paper.strand)
        self._clear()
        self._text(paper.title, "emptyTitle")
        year = f"{paper.year} (circulated {paper.circulated})" if paper.circulated else str(paper.year)
        self._text(f"{', '.join(paper.authors)} · {year} · {paper.venue}", "secondary")
        badges = QHBoxLayout()
        badges.setSpacing(SPACE_S)
        badges.addWidget(Badge(strand, "info"))
        badges.addWidget(Badge("✓ In your code", "good") if paper.implemented
                         else Badge("Not in the code yet", "neutral"))
        badges.addStretch(1)
        self._details.addLayout(badges)
        self._section("The question", paper.question)
        self._section("What it changed", paper.changed)
        self._section("What it assumed", paper.assumed)
        self._links("Builds on", [(LINK_KINDS[l.kind], l.to, self._canon.paper(l.to).label)
                                  for l in paper.links])
        self._links("Later work", [(INCOMING[l.kind], p.id, p.label)
                                   for p, l in sorted(self._canon.cited_by(paper_id),
                                                      key=lambda pl: pl[0].when)])
        if paper.lives_in:
            self._section("In your code", "\n".join(path.removeprefix("quant_hub/")
                                                     for path in paper.lives_in))
        self._details.addSpacing(SPACE_S)
        self._details.addWidget(divider())
        reading = SelectField("Reading status", _STATUSES, mine.get("status", "To read"), _INNER)
        reading.select.current_changed.connect(lambda s, pid=paper_id: self._save(pid, {"status": s}))
        self._details.addWidget(reading)
        runs = len(mine.get("run_ids", []))
        if runs:
            self._text(f"Linked to {runs} saved run{'s' if runs != 1 else ''}; open "
                       f"{'them' if runs != 1 else 'it'} from the Papers tab.", "muted")
        actions = [button("Open PDF", "primary", "ph.file-pdf", lambda: self._open_pdf(paper_id)),
                   button("Open source", icon_name="ph.arrow-square-out",
                          on_click=lambda: self._open_source(paper_id)),
                   button("Attach PDF…", icon_name="ph.paperclip", on_click=lambda: self._attach(paper_id))]
        actions[0].setEnabled(bool(mine.get("pdf_available")))
        actions[1].setEnabled(bool(paper.source_url))
        for action in actions:
            action.setFixedWidth(_HALF)
        self._details.addLayout(form_grid(actions, 2))
        if paper.verified.via in _VERIFIED:
            check = f"{_VERIFIED[paper.verified.via]} ({paper.verified.checked})."
        else:
            check = f"Not machine-checked: {paper.verified.note}"
        self._text(check, "muted")
        self._details.addStretch(1)

    # -- your layer ---------------------------------------------------------
    def _save(self, paper_id: str, change: dict, pdf_path: str | None = None):
        if self._busy:
            return
        self._busy = True
        paper = {**self._papers[paper_id], **change}
        def done(_saved):
            self._busy = False
            self._status("Paper saved")
            self.refresh()
        self._tasks.submit(lambda: self._service.save_paper(paper, pdf_path),
                           on_done=done, on_error=self._error)

    def _attach(self, paper_id: str):
        path, _ = QFileDialog.getOpenFileName(self, "Attach a PDF to this paper", "", "PDF files (*.pdf)")
        if path:
            self._save(paper_id, {}, path)

    def _open(self, paper_id: str):
        if self._papers.get(paper_id, {}).get("pdf_available"):
            self._open_pdf(paper_id)
        else:
            self._open_source(paper_id)

    def _open_pdf(self, paper_id: str):
        paper = dict(self._papers.get(paper_id, {}))
        self._tasks.submit(lambda: self._service.paper_path(paper),
                           on_done=lambda path: QDesktopServices.openUrl(QUrl.fromLocalFile(path)),
                           on_error=self._error)

    def _open_source(self, paper_id: str):
        url = QUrl(self._canon.paper(paper_id).source_url)
        if url.scheme() in ("https", "http") and url.host():
            QDesktopServices.openUrl(url)
        else:
            self._error(ValueError("This paper has no online source"))
