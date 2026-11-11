"""The timeline's presentation math (Lit-2): the axis, spreading, labels."""
import numpy as np
import pytest

from quant_hub.gui.timeline import (EARLY, PIVOT, LabelBox, _TICK_SPACING, from_axis, pack_labels,
                                    spread, to_axis, year_ticks)


def test_the_axis_is_identity_after_the_pivot_and_squeezed_before():
    assert to_axis(1973.5) == 1973.5 and to_axis(PIVOT) == PIVOT
    assert to_axis(1900) == pytest.approx(PIVOT - 0.3 * (PIVOT - EARLY))
    years = np.linspace(1700, 2030, 3301)
    u = np.array([to_axis(y) for y in years])
    assert np.all(np.diff(u) > 0)                                  # strictly increasing
    for knot in (EARLY, PIVOT):                                    # continuous at the knots
        assert to_axis(knot - 1e-9) == pytest.approx(to_axis(knot), abs=1e-6)
    np.testing.assert_allclose([from_axis(v) for v in u], years, atol=1e-9)
    # the squeeze: fifty years before 1950 take fifteen units, 162 before 1900 about eight
    assert to_axis(1950) - to_axis(1900) == pytest.approx(15)
    assert to_axis(1900) - to_axis(1738) == pytest.approx(8.1)


def test_same_year_papers_spread_inside_their_year():
    xs = spread([(0, 1973), (0, 1973), (1, 1973), (0, 1973), (0, 1990)])
    assert xs[2] == 1973.5 and xs[4] == 1990.5                     # alone: mid-year
    same = [xs[0], xs[1], xs[3]]
    assert same == sorted(same) and len(set(same)) == 3
    assert all(1973 < x < 1974 for x in same)


def boxes(*specs):
    return [LabelBox(f"p{i}", lane, center, width, priority)
            for i, (lane, center, width, priority) in enumerate(specs)]


def test_labels_never_overlap_and_the_most_cited_win():
    labels = boxes((0, 100, 80, 1), (0, 130, 80, 5), (0, 160, 80, 3), (1, 130, 80, 0))
    shown = pack_labels(labels, rows=1, gap=6, visible=(0, 1000))
    assert shown == {"p1": (0, 90.0), "p3": (0, 90.0)}             # p1 wins its lane; p3 is alone
    shown = pack_labels(labels, rows=2, gap=6, visible=(0, 1000))
    assert shown["p1"][0] == 0 and shown["p2"][0] == 1             # the runner-up takes row 1
    rng = np.random.default_rng(7)
    many = boxes(*[(int(rng.integers(3)), float(rng.uniform(0, 600)), float(rng.uniform(40, 120)),
                    float(rng.uniform())) for _ in range(200)])
    shown = pack_labels(many, rows=2, gap=6, visible=(0, 600))
    by_row = {}
    for box in many:
        if box.id in shown:
            row, left = shown[box.id]
            assert 0 <= left and left + box.width <= 600           # inside the plot
            by_row.setdefault((box.lane, row), []).append((left, left + box.width))
    for spans in by_row.values():
        spans.sort()
        assert all(b[0] - a[1] >= 6 - 1e-9 for a, b in zip(spans, spans[1:]))


def test_labels_slide_inward_at_the_edges_and_skip_dots_out_of_view():
    shown = pack_labels(boxes((0, 5, 80, 1), (0, 598, 80, 1), (0, 700, 80, 9)), rows=1, gap=6,
                        visible=(0, 600))
    assert shown == {"p0": (0, 0), "p1": (0, 520)}
    assert pack_labels(boxes((0, 100, 80, 1)), rows=0, gap=6, visible=(0, 600)) == {}


def test_year_ticks_keep_their_spacing_across_the_squeeze():
    low, high, px = to_axis(1730), to_axis(2027), 600
    ticks = year_ticks(low, high, px)
    assert ticks == sorted(ticks) and from_axis(low) <= ticks[0] and ticks[-1] <= 2027
    per_unit = px / (high - low)
    assert all((to_axis(b) - to_axis(a)) * per_unit >= _TICK_SPACING for a, b in zip(ticks, ticks[1:]))
    close = year_ticks(1970, 1980, 600)                            # zoomed in: yearly or two-yearly
    assert len(close) >= 5
