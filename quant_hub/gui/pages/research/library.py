"""Paper catalog presentation. Disk operations always use TaskRunner."""
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QVBoxLayout, QWidget

from quant_hub.gui.components import Card, DataTable, Field, SelectField, TextArea, button, form_grid, label
from quant_hub.gui.theme import SIDE_PANEL_WIDTH, SPACE_L, SPACE_M

_HALF = (SIDE_PANEL_WIDTH - 2 * SPACE_L - SPACE_M) // 2
_FULL = 2 * _HALF + SPACE_M


class PaperLibrary(QWidget):
    def __init__(self, service, tasks, status, current_run, open_run):
        super().__init__()
        self._service, self._tasks, self._status = service, tasks, status
        self._current_run, self._open_run = current_run, open_run
        self._papers, self._shown, self._selected = [], [], None
        self._busy = False
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SPACE_L)
        catalog = Card("Paper library", "Search titles, authors, dates, topics and your notes")
        self._search = Field("Search library", width=_FULL, placeholder="Bachelier, volatility, factors…")
        self._search.edit.textChanged.connect(self._filter)
        catalog.body.addWidget(self._search)
        self._table = DataTable([("Title", "left"), ("Year", "left"), ("Status", "left"), ("File", "left")], stretch=0)
        self._table.setMinimumHeight(440)
        self._table.row_activated.connect(self._select)
        catalog.body.addWidget(self._table, 1)
        catalog.add_action(button("Import PDFs…", icon_name="ph.upload-simple", on_click=self._import))
        catalog.body.addWidget(label("Foundational entries link to their sources. Import your own PDFs to keep a local copy.", "muted", wrap=True))
        outer.addWidget(catalog, 1)
        editor = Card("Reading desk", "Metadata, notes and links to your experiments")
        editor.setFixedWidth(SIDE_PANEL_WIDTH)
        self._title = Field("Title", width=_FULL)
        self._authors = Field("Authors", width=_FULL)
        self._year = Field("Year", width=_HALF)
        self._topic = Field("Topic", width=_HALF)
        self._reading = SelectField("Reading status", ["To read", "Reading", "Read", "Replicated"], width=_FULL)
        self._url = Field("Source / DOI URL", width=_FULL)
        self._notes = TextArea(placeholder="Key result, assumptions, questions, replication notes…")
        self._notes.setMinimumHeight(150)
        self._notes.setMaximumHeight(150)
        editor.body.addWidget(self._title)
        editor.body.addWidget(self._authors)
        editor.body.addLayout(form_grid([self._year, self._topic], 2))
        editor.body.addWidget(self._reading)
        editor.body.addWidget(self._url)
        editor.body.addWidget(self._notes)
        self._links = label("No linked experiments", "muted", wrap=True)
        editor.body.addWidget(self._links)
        self._actions = []
        save = button("Save paper", "primary", on_click=self._save)
        editor.body.addWidget(save)
        self._actions.append(save)
        for text, fn in [("New reference", self._new), ("Attach PDF…", self._attach),
                         ("Open PDF", self._open_pdf), ("Open source", self._open_source),
                         ("Link run", self._link), ("Open linked run", self._open_link)]:
            action = button(text, "primary" if text == "Save paper" else None, on_click=fn)
            self._actions.append(action)
        editor.body.addLayout(form_grid(self._actions[1:], 2))
        editor.body.addStretch(1)
        outer.addWidget(editor)

    def refresh(self):
        self._tasks.submit(self._service.papers, on_done=self._loaded, on_error=self._error)

    def _loaded(self, papers):
        self._papers = papers
        self._filter()

    def _filter(self, *_):
        query = self._search.text().casefold()
        self._shown = [p for p in self._papers if query in " ".join(str(v) for v in p.values()).casefold()]
        self._table.set_rows([[p["title"], p.get("year", ""), p.get("status", "To read"),
                               ("Local PDF" if p.get("pdf_available") else "Missing PDF")
                               if p.get("pdf") else "Reference"] for p in self._shown])

    def _select(self, row):
        if not 0 <= row < len(self._shown):
            return
        self._selected = dict(self._shown[row])
        for field, key in [(self._title, "title"), (self._authors, "authors"), (self._year, "year"),
                           (self._topic, "topic"), (self._url, "url")]:
            field.set_text(self._selected.get(key, ""))
            field.edit.setCursorPosition(0)
            field.edit.setToolTip(field.text())
        self._reading.select.set_items(["To read", "Reading", "Read", "Replicated"], self._selected.get("status", "To read"))
        self._notes.setPlainText(self._selected.get("notes", ""))
        linked = self._selected.get("run_ids", [])
        self._links.setText(f"{len(linked)} linked experiment(s)" if linked else "No linked experiments")

    def _new(self):
        self._selected = None
        for field in (self._title, self._authors, self._year, self._topic, self._url):
            field.set_text("")
        self._notes.clear()
        self._links.setText("No linked experiments")
        self._reading.select.set_items(["To read", "Reading", "Read", "Replicated"], "To read")

    def _metadata(self):
        return {**(self._selected or {}), "title": self._title.required_text(),
                "authors": self._authors.text(), "year": self._year.text(), "topic": self._topic.text(),
                "url": self._url.text(), "status": self._reading.current(), "notes": self._notes.toPlainText()}

    def _save(self, pdf_path=None, link=None):
        if self._busy:
            return
        try:
            paper = self._metadata()
        except ValueError as exc:
            self._error(exc)
            return
        if link:
            paper["run_ids"] = list(dict.fromkeys([*paper.get("run_ids", []), link]))
        self._busy = True
        self._tasks.submit(lambda: self._service.save_paper(paper, pdf_path),
                           on_done=self._saved, on_error=self._error)

    def _saved(self, paper):
        self._busy = False
        self._selected = paper
        self._links.setText(f"{len(paper.get('run_ids', []))} linked experiment(s)")
        self._status("Paper saved")
        self.refresh()

    def _import(self):
        if self._busy:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Import papers", "", "PDF files (*.pdf)")
        if paths:
            self.import_paths(paths)

    def import_paths(self, paths):
        self._busy = True
        def work():
            return [self._service.save_paper({"title": Path(path).stem}, path) for path in paths]
        def done(papers):
            self._busy = False
            self._status(f"Imported {len(papers)} PDF(s)")
            self.refresh()
        self._tasks.submit(work, on_done=done, on_error=self._error)

    def _attach(self):
        path, _ = QFileDialog.getOpenFileName(self, "Attach PDF to this paper", "", "PDF files (*.pdf)")
        if path:
            self._save(pdf_path=path)

    def _link(self):
        current = self._current_run()
        if current:
            self._save(link=current)
        else:
            self._error(ValueError("Run or reopen an experiment first"))

    def _open_link(self):
        linked = (self._selected or {}).get("run_ids", [])
        if linked:
            self._open_run(linked[-1])
        else:
            self._error(ValueError("This paper has no linked experiments"))

    def _open_pdf(self):
        if not self._selected:
            self._error(ValueError("Select a paper first"))
            return
        paper = dict(self._selected)
        self._tasks.submit(lambda: self._service.paper_path(paper),
                           on_done=lambda path: QDesktopServices.openUrl(QUrl.fromLocalFile(path)),
                           on_error=self._error)

    def _open_source(self):
        url = QUrl(self._url.text())
        if url.scheme() in ("https", "http") and url.host():
            QDesktopServices.openUrl(url)
        else:
            self._error(ValueError("Enter an https:// source or DOI URL"))

    def _error(self, exc):
        self._busy = False
        self._status(str(exc), error=True)
