"""Searchable saved experiments; reopening goes through the service."""
from PySide6.QtWidgets import QVBoxLayout, QWidget

from quant_hub.gui.components import Card, DataTable, Field, button, label
from quant_hub.gui.theme import FIELD_L


class SavedRuns(QWidget):
    def __init__(self, service, tasks, status, open_run):
        super().__init__()
        self._service, self._tasks, self._status, self._open_run = service, tasks, status, open_run
        self._runs, self._shown = [], []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        card = Card("Research log", "Every completed experiment and every sweep trial is retained")
        self._search = Field("Search runs and conclusions", width=2 * FIELD_L)
        self._search.edit.textChanged.connect(self._filter)
        card.body.addWidget(self._search)
        self._table = DataTable([("Created · UTC", "left"), ("Experiment", "left"),
                                 ("Conclusion / notes", "left")], stretch=1)
        self._table.setMinimumHeight(430)
        self._table.row_activated.connect(lambda row: self._open_run(self._shown[row]["id"]) if 0 <= row < len(self._shown) else None)
        card.body.addWidget(self._table, 1)
        card.add_action(button("Refresh", on_click=self.refresh))
        card.body.addWidget(label("Select a run to replay its saved prices, positions and costs. Market data is not fetched again.", "muted", wrap=True))
        layout.addWidget(card)

    def refresh(self):
        self._tasks.submit(self._service.list_runs, on_done=self._loaded,
                           on_error=lambda e: self._status(str(e), error=True))

    def _loaded(self, runs):
        self._runs = runs
        self._filter()

    def _filter(self, *_):
        query = self._search.text().casefold()
        self._shown = [r for r in self._runs if query in (r["title"] + " " + r["notes"]).casefold()]
        self._table.set_rows([[r["created_at"][:19].replace("T", " "), r["title"],
                              r["notes"].replace("\n", " ")] for r in self._shown])
