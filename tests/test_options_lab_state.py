"""The Options Lab migrates the old single-card calculator's saved state."""

from quant_hub.gui.views.options_lab.view import _migrate


def test_flat_calculator_state_maps_onto_sections():
    old = {"spot": "420", "strike": "400", "dte": "45", "rate": "6", "sigma": "22",
           "option_type": "put"}
    new = _migrate(old)
    assert new["market"] == {"spot": "420", "vol": "22", "rate": "6"}
    assert new["pricer"] == {"strike": "400", "dte": "45", "option_type": "put"}


def test_current_and_empty_state_pass_through():
    current = {"market": {"spot": "1"}, "pricer": {}}
    assert _migrate(current) is current
    assert _migrate({}) == {}
