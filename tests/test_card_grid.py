"""The card grid (D56): placement, minimums, hidden cards, moves, resizes and
saving, headless. Units: at 1240 px a column is (1240 - 11*16)/12 px wide, a
row 24 px tall, with 16 px between."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout  # noqa: E402

from quant_hub.gui.card_grid import CardGrid, Place  # noqa: E402

WIDTH = 1240


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(["test"])


def _card(min_w: int = 0, min_h: int = 0) -> QFrame:
    card = QFrame()
    layout = QVBoxLayout(card)
    layout.addWidget(QLabel("A card"))
    card.setMinimumSize(min_w, min_h)
    return card


def _grid(app, *cards, width: int = WIDTH) -> CardGrid:
    """``cards``: (name, x, y, w, h[, min_w[, min_h]])."""
    grid = CardGrid()
    for name, x, y, w, h, *minimums in cards:
        grid.add(name, _card(*minimums), x, y, w, h)
    grid.resize(width, 900)
    grid.show()
    app.processEvents()
    return grid


def _no_overlaps(grid: CardGrid, names) -> bool:
    places = [grid.shown_place(n) for n in names if grid.shown_place(n) is not None]
    return not any(a.overlaps(b) for i, a in enumerate(places) for b in places[i + 1:])


def test_cards_land_where_they_are_placed(app):
    grid = _grid(app, ("a", 0, 0, 6, 4), ("b", 6, 0, 6, 4), ("c", 0, 4, 12, 3))
    cell = (WIDTH - 11 * 16) / 12
    a, b, c = (grid._cards[n].geometry() for n in "abc")
    assert (a.x(), a.y(), a.width(), a.height()) == (0, 0, round(6 * cell + 5 * 16), 4 * 24 + 3 * 16)
    assert b.x() == round(6 * (cell + 16)) and b.y() == 0
    assert c.y() == 4 * 40 and c.width() == round(12 * cell + 11 * 16)
    assert grid.minimumSizeHint().height() == 4 * 40 + 3 * 24 + 2 * 16


def test_cards_fall_up_into_gaps(app):
    grid = _grid(app, ("a", 0, 5, 12, 2))
    assert grid.shown_place("a") == Place(0, 0, 12, 2)
    assert grid.place("a") == Place(0, 5, 12, 2)          # where it was put is kept


def test_a_card_is_never_narrower_than_its_content(app):
    grid = _grid(app, ("a", 0, 0, 3, 2, 500), ("b", 3, 0, 9, 2))
    assert grid._cards["a"].width() >= 500
    assert grid.shown_place("a").w == 5                     # ceil(516 / 104.67)
    assert _no_overlaps(grid, "ab")
    assert grid.place("a").w == 3                           # the user's width is kept
    grid.resize(3000, 900)
    app.processEvents()
    assert grid.shown_place("a").w == 3                     # wide enough again


def test_a_card_is_never_shorter_than_its_content(app):
    grid = _grid(app, ("a", 0, 0, 6, 1, 0, 200))
    assert grid.shown_place("a").h == 6                     # ceil((200 + 16) / 40)
    assert grid._cards["a"].height() >= 200


def test_hidden_cards_take_no_room(app):
    grid = _grid(app, ("a", 0, 0, 12, 2), ("b", 0, 2, 12, 2))
    grid._cards["a"].hide()
    app.processEvents()
    assert grid.shown_place("a") is None and grid.shown_place("b").y == 0
    grid._cards["a"].show()
    app.processEvents()
    assert grid.shown_place("b").y == 2


def test_moving_a_card_pushes_the_others_down(app):
    grid = _grid(app, ("a", 0, 0, 12, 2), ("b", 0, 2, 12, 2), ("c", 0, 4, 6, 2))
    saved = []
    grid.changed.connect(lambda: saved.append(grid.state()))
    grid.move_card("c", 0, 0)
    assert grid.shown_place("c").y == 0 and grid.shown_place("a").y == 2
    assert grid.shown_place("b").y == 4
    assert _no_overlaps(grid, "abc")
    assert saved and saved[-1]["cards"]["c"][:2] == [0, 0]


def test_resizing_a_card_snaps_and_makes_room(app):
    grid = _grid(app, ("a", 0, 0, 6, 2), ("b", 6, 0, 6, 2))
    grid.resize_card("a", 8, 3)
    assert grid.shown_place("a") == Place(0, 0, 8, 3)
    assert grid.shown_place("b").y == 3                     # no room beside, so below
    assert _no_overlaps(grid, "ab")


def test_the_layout_round_trips_and_drops_cards_it_doesnt_know(app):
    first = _grid(app, ("a", 0, 0, 6, 2), ("b", 6, 0, 6, 2))
    first.move_card("b", 0, 0)
    saved = first.state()
    saved["cards"]["gone"] = [0, 0, 3, 3]
    second = _grid(app, ("a", 0, 0, 6, 2), ("b", 6, 0, 6, 2))
    second.restore(saved)
    app.processEvents()
    assert second.shown_place("b") == first.shown_place("b")
    assert "gone" not in second.state()["cards"]
    third = _grid(app, ("a", 0, 0, 6, 2), ("b", 6, 0, 6, 2))
    third.restore({**saved, "version": 99})                 # another version: defaults
    app.processEvents()
    assert third.place("b") == Place(6, 0, 6, 2)


def test_reset_goes_back_to_the_pages_layout(app):
    grid = _grid(app, ("a", 0, 0, 6, 2), ("b", 6, 0, 6, 2))
    grid.resize_card("a", 12, 4)
    changes = []
    grid.changed.connect(lambda: changes.append(True))
    grid.reset()
    assert grid.place("a") == Place(0, 0, 6, 2) and changes


def test_dragging_a_card_by_its_title_moves_it(app):
    grid = _grid(app, ("a", 0, 0, 12, 2), ("b", 0, 2, 12, 2))
    title = grid._cards["a"].findChild(QLabel)
    start = QPoint(10, 5)
    QTest.mousePress(title, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(title, start + QPoint(0, 60))           # past the drag distance
    QTest.mouseMove(title, start + QPoint(0, 300))          # well below b
    assert grid._ghost.isVisible()
    QTest.mouseRelease(title, Qt.LeftButton, Qt.NoModifier, start + QPoint(0, 300))
    app.processEvents()
    assert grid.shown_place("b").y == 0 and grid.shown_place("a").y == 2
    assert not grid._ghost.isVisible()


def test_dragging_the_grip_resizes_a_card(app):
    grid = _grid(app, ("a", 0, 0, 4, 2))
    grip = next(g for g, name in grid._grips.items() if name == "a")
    start = QPoint(5, 5)
    QTest.mousePress(grip, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(grip, start + QPoint(30, 0))
    QTest.mouseMove(grip, start + QPoint(2 * 105, 2 * 40))  # two columns and two rows more
    QTest.mouseRelease(grip, Qt.LeftButton, Qt.NoModifier, start + QPoint(2 * 105, 2 * 40))
    app.processEvents()
    assert grid.shown_place("a") == Place(0, 0, 6, 4)


def test_a_click_without_a_drag_changes_nothing(app):
    grid = _grid(app, ("a", 0, 0, 6, 2))
    changes = []
    grid.changed.connect(lambda: changes.append(True))
    title = grid._cards["a"].findChild(QLabel)
    QTest.mouseClick(title, Qt.LeftButton)
    assert changes == [] and grid.place("a") == Place(0, 0, 6, 2)


def test_a_bad_place_is_refused(app):
    grid = CardGrid()
    with pytest.raises(ValueError):
        grid.add("a", _card(), 10, 0, 4, 2)                  # past the last column
    grid.add("b", _card(), 0, 0, 4, 2)
    with pytest.raises(ValueError):
        grid.add("b", _card(), 0, 0, 4, 2)                   # two cards, one name
