"""The DCF (W4): the owner's dcfmodeler tests, ported, plus the plan's checks.

Ported from ``dcfmodeler/tests``: ``test_engine`` (7), ``test_analysis`` (6)
and ``test_batch``'s seeding (3). ``test_adapter`` and ``test_serialize``
carry into W5 (the SEC adapter) and W6 (the screen), ``rank_screen``'s test
with them.
"""

import math
from dataclasses import replace
from datetime import date

import numpy as np
import pytest

from quant_hub.data.fundamentals import FinancialHistory, YearData
from quant_hub.models.dcf import (
    LAST_YEAR,
    STEADY_STATE,
    Assumptions,
    BatchConfig,
    ValuationError,
    assumptions_from_history,
    calibration,
    cash_flows_positive,
    growth_monotone,
    implied_growth,
    implied_wacc,
    market_comparison,
    multiples,
    sensitivity,
    value,
    years_since,
)


# ------------------------------------------------------- ported: test_engine.py
def engine_hist(**latest):
    base = dict(fiscal_year=2024, period_end="2024-12-31", revenue=1000.0,
                total_debt=200.0, cash_and_equivalents=0.0)
    base.update(latest)
    h = FinancialHistory(shares_diluted=100.0)
    h.years.append(YearData(**base))
    return h


class TestEngine:
    def test_golden_value_per_share(self):
        # base_rev=1000 shares=100 net_debt=200; 2yr; g=10% margin=20% tax=25%
        # da=5% capex=5% nwc=10%; wacc=10% term_g=2%. Worked by hand below.
        a = Assumptions(projection_years=2, revenue_growth=0.10, ebit_margin=0.20,
                        tax_rate=0.25, da_pct_revenue=0.05, capex_pct_revenue=0.05,
                        nwc_pct_revenue=0.10, wacc=0.10, terminal_growth=0.02)
        r = value(engine_hist(), a)
        # Year 1: rev 1100, ebit 220, nopat 165, da 55, capex 55, dNWC 10 -> ufcf 155
        assert math.isclose(r.projection[0].ufcf, 155.0)
        # Year 2: rev 1210, ebit 242, nopat 181.5, da 60.5, capex 60.5, dNWC 11 -> 170.5
        assert math.isclose(r.projection[1].ufcf, 170.5)
        assert math.isclose(r.pv_explicit, 155 / 1.1 + 170.5 / 1.21)
        tv = 170.5 * 1.02 / 0.08
        assert math.isclose(r.terminal_value, tv)
        assert math.isclose(r.pv_terminal, tv / 1.21)
        assert math.isclose(r.equity_value, r.enterprise_value - 200.0)
        assert math.isclose(r.value_per_share, r.equity_value / 100.0)
        assert round(r.value_per_share, 3) == 18.784

    def test_scalar_and_list_paths_agree(self):
        flat = Assumptions(projection_years=3, revenue_growth=0.08)
        listed = Assumptions(projection_years=3, revenue_growth=[0.08, 0.08, 0.08])
        assert math.isclose(value(engine_hist(), flat).value_per_share,
                            value(engine_hist(), listed).value_per_share)

    def test_path_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            value(engine_hist(), Assumptions(projection_years=3, revenue_growth=[0.08, 0.08]))

    def test_terminal_growth_ge_wacc_raises(self):
        with pytest.raises(ValueError):
            value(engine_hist(), Assumptions(wacc=0.05, terminal_growth=0.05))

    def test_loud_failure_lists_missing_fields(self):
        h = FinancialHistory()
        h.note_missing("revenue", "no XBRL revenue tag matched")
        h.note_missing("shares_diluted", "dei: shares tag absent")
        h.note_missing("total_debt", "no debt tag on balance sheet")
        with pytest.raises(ValuationError) as ei:
            value(h, Assumptions())
        msg = str(ei.value)
        assert "base_revenue" in msg and "shares" in msg and "net_debt" in msg
        assert "no XBRL revenue tag matched" in msg

    def test_overrides_bypass_missing_data(self):
        a = Assumptions(base_revenue_override=500.0, shares_override=10.0,
                        net_debt_override=0.0)
        assert value(FinancialHistory(), a).value_per_share > 0

    def test_sensitivity_grid_marks_invalid_cells(self):
        t = sensitivity(engine_hist(), Assumptions(), waccs=[0.08, 0.10], growths=[0.02, 0.08])
        assert math.isnan(t.grid[0, 1])                    # g=0.08 >= wacc=0.08
        assert not math.isnan(t.grid[1, 0])


# ----------------------------------------------------- ported: test_analysis.py
def analysis_hist():
    h = FinancialHistory(shares_diluted=100.0)
    h.years.append(YearData(2022, "2022-12-31", revenue=800.0, operating_income=160.0,
                            capex=40.0, depreciation_amortization=32.0,
                            income_before_tax=150.0, income_tax_expense=30.0))
    h.years.append(YearData(2023, "2023-12-31", revenue=900.0, operating_income=189.0,
                            capex=45.0, depreciation_amortization=36.0,
                            income_before_tax=170.0, income_tax_expense=34.0))
    h.years.append(YearData(2024, "2024-12-31", revenue=1000.0, operating_income=210.0,
                            capex=50.0, depreciation_amortization=40.0,
                            income_before_tax=190.0, income_tax_expense=38.0,
                            total_debt=200.0, cash_and_equivalents=0.0))
    return h


class TestAnalysis:
    def test_implied_growth_round_trips(self):
        a = Assumptions(revenue_growth=0.05)
        target = value(analysis_hist(), a).value_per_share
        roots = implied_growth(analysis_hist(), a, target)
        assert len(roots) == 1 and math.isclose(roots[0], 0.05, abs_tol=1e-6)

    def test_implied_wacc_round_trips(self):
        a = Assumptions(wacc=0.09)
        target = value(analysis_hist(), a).value_per_share
        roots = implied_wacc(analysis_hist(), a, target)
        assert len(roots) == 1 and math.isclose(roots[0], 0.09, abs_tol=1e-6)

    def test_implied_growth_unbracketed_returns_none(self):
        assert implied_growth(analysis_hist(), Assumptions(), 10_000_000.0) == ()

    def test_margin_of_safety_sign(self):
        r = value(analysis_hist(), Assumptions())
        v = r.value_per_share
        cheap = market_comparison(r, v * 0.5)
        rich = market_comparison(r, v * 2.0)
        assert cheap.margin_of_safety > 0 > rich.margin_of_safety
        assert math.isclose(cheap.market_cap, v * 0.5 * r.shares)

    def test_diagnostics_cagr_and_margins(self):
        c = calibration(analysis_hist())
        assert math.isclose(c.revenue_cagr, (1000 / 800) ** 0.5 - 1, abs_tol=1e-6)
        assert math.isclose(c.tax_rate, 0.20, abs_tol=1e-3)
        assert 0.20 <= c.ebit_margin <= 0.21

    def test_multiples_present(self):
        m = multiples(value(analysis_hist(), Assumptions()))
        assert m.ev_revenue > 0 and m.fcf_yield_ev > 0


# ------------------------------------------------- ported: test_batch.py (seeding)
def batch_hist(**over):
    h = FinancialHistory(company="Demo", ticker="DEMO", shares_diluted=100.0)
    for b in (dict(fiscal_year=2023, period_end="2023-12-31", revenue=800.0,
                   operating_income=160.0, capex=40.0, depreciation_amortization=32.0,
                   income_before_tax=150.0, income_tax_expense=30.0),
              dict(fiscal_year=2024, period_end="2024-12-31", revenue=1000.0,
                   operating_income=210.0, capex=50.0, depreciation_amortization=40.0,
                   income_before_tax=190.0, income_tax_expense=38.0)):
        b.update(over)
        h.years.append(YearData(**b))
    return h


class TestSeeding:
    def test_seed_uses_historical_calibration(self):
        a = assumptions_from_history(batch_hist())
        assert math.isclose(a.revenue_growth, 0.25, abs_tol=1e-6)      # 800 -> 1000 in a year
        assert math.isclose(a.ebit_margin, 0.205, abs_tol=1e-6)        # 20% and 21%
        assert math.isclose(a.tax_rate, 0.20, abs_tol=1e-6)

    def test_seed_falls_back_when_calibration_missing(self):
        h = FinancialHistory(shares_diluted=10.0)
        h.years.append(YearData(2024, "2024-12-31", revenue=500.0))
        a = assumptions_from_history(h, BatchConfig(default_growth=0.03, default_margin=0.15,
                                                    default_tax=0.21))
        assert (a.revenue_growth, a.ebit_margin, a.tax_rate) == (0.03, 0.15, 0.21)

    def test_tax_rate_is_clamped(self):
        h = batch_hist(income_before_tax=10.0, income_tax_expense=40.0)
        assert assumptions_from_history(h, BatchConfig(tax_clamp=0.50)).tax_rate == 0.50


# ------------------------------------------------------------- the plan's checks
def plain_hist(revenue=1000.0, debt=0.0, cash=0.0, securities=None):
    return FinancialHistory(shares_diluted=100.0, years=[YearData(
        2024, "2024-12-31", revenue=revenue, total_debt=debt, cash_and_equivalents=cash,
        marketable_securities=securities)])


class TestClosedForms:
    @pytest.mark.parametrize("method", [LAST_YEAR, STEADY_STATE])
    @pytest.mark.parametrize("years", [1, 3, 5, 10])
    def test_gordon_identity(self, method, years):
        a = Assumptions(projection_years=years, revenue_growth=0.03, terminal_growth=0.03,
                        wacc=0.09, terminal_method=method)
        r = value(plain_hist(), a)
        assert r.enterprise_value == pytest.approx(r.projection[0].ufcf / (0.09 - 0.03),
                                                   rel=1e-12)

    def test_steady_state_terminal_flow_is_its_closed_form(self):
        a = Assumptions(revenue_growth=0.15)
        last = value(plain_hist(), a)
        steady = value(plain_hist(), replace(a, terminal_method=STEADY_STATE))
        k = 0.20 * (1 - 0.21) + 0.04 - 0.05
        rev_n = 1000 * 1.15**5
        assert steady.terminal_flow == pytest.approx(k * rev_n * 1.025 - 0.10 * 0.025 * rev_n)
        assert last.terminal_flow == pytest.approx(last.projection[-1].ufcf * 1.025)
        assert steady.terminal_flow / last.terminal_flow - 1 == pytest.approx(0.0786, abs=5e-4)
        assert last.terminal_share == pytest.approx(0.778, abs=1e-3)

    def test_valuation_date_rolls_forward(self):
        a = Assumptions()
        at_year_end = value(plain_hist(debt=150.0), a)
        later = value(plain_hist(debt=150.0), replace(a, valuation_years=0.5))
        assert later.enterprise_value == pytest.approx(at_year_end.enterprise_value * 1.09**0.5,
                                                       rel=1e-12)
        assert later.value_per_share == pytest.approx(
            (later.enterprise_value - 150.0) / 100.0)
        assert years_since("2024-09-28", date(2025, 10, 28)) == pytest.approx(395 / 365.25)


class TestReverseDCF:
    def test_working_capital_heavy_growth_has_two_implied_rates(self):
        a = Assumptions(nwc_pct_revenue=0.60)              # n > k = 14.8%
        assert not growth_monotone(a)
        roots = implied_growth(plain_hist(), a, 18.0)
        assert len(roots) == 2
        assert roots[0] == pytest.approx(-0.149, abs=2e-3)
        assert roots[1] == pytest.approx(0.159, abs=2e-3)
        for g in roots:
            assert value(plain_hist(), replace(a, revenue_growth=g)).value_per_share == \
                pytest.approx(18.0, abs=1e-6)

    def test_the_monotone_case_has_one_root(self):
        a = Assumptions(nwc_pct_revenue=0.10)              # k = 14.8% >= n
        assert growth_monotone(a)
        price = value(plain_hist(), replace(a, revenue_growth=0.12)).value_per_share
        assert implied_growth(plain_hist(), a, price) == pytest.approx((0.12,), abs=1e-7)

    def test_implied_wacc_is_unique_with_positive_flows(self):
        a = Assumptions()
        r = value(plain_hist(), a)
        assert cash_flows_positive(r)
        assert implied_wacc(plain_hist(), a, r.value_per_share) == pytest.approx((0.09,),
                                                                                abs=1e-7)


class TestBridge:
    def test_net_debt_can_net_marketable_securities(self):
        h = plain_hist(debt=500.0, cash=100.0, securities=300.0)
        plain = value(h, Assumptions())
        netted = value(h, Assumptions(net_marketable_securities=True))
        assert plain.net_debt == 400.0 and netted.net_debt == 100.0
        assert netted.value_per_share - plain.value_per_share == pytest.approx(3.0)

    def test_netting_securities_the_filing_lacks_is_loud(self):
        with pytest.raises(ValuationError, match="marketable_securities"):
            value(plain_hist(debt=500.0), Assumptions(net_marketable_securities=True))

    def test_the_latest_year_is_the_newest_whatever_the_order(self):
        # The original adapter appended the newest year first, and latest()
        # took the last element: the oldest year, valued as if current.
        h = FinancialHistory(shares_diluted=100.0)
        for offset, rev in enumerate((1000.0, 900.0, 800.0)):
            h.years.append(YearData(2025 - offset, f"{2025 - offset}-09-30", revenue=rev,
                                    total_debt=200.0 if offset == 0 else None,
                                    cash_and_equivalents=0.0 if offset == 0 else None))
        assert h.latest().fiscal_year == 2025
        assert value(h, Assumptions()).base_revenue == 1000.0

    def test_multiples_with_zero_denominators(self):
        r = value(plain_hist(), Assumptions(ebit_margin=0.0))
        m = multiples(r)
        assert m.ev_ebit is None and m.price_nopat is None
        # No EBIT, capex above D&A: every flow negative, so EV is too.
        assert not cash_flows_positive(r) and r.enterprise_value < 0 and m.ev_revenue < 0


class TestRejections:
    def test_bad_assumptions(self):
        h = plain_hist()
        for bad in (Assumptions(projection_years=0), Assumptions(projection_years=True),
                    Assumptions(wacc=float("nan")), Assumptions(terminal_method="perpetuity"),
                    Assumptions(valuation_years=1.0), Assumptions(valuation_years=-0.1),
                    Assumptions(shares_override=0.0), Assumptions(ebit_margin=[0.2, math.inf,
                                                                               0.2, 0.2, 0.2])):
            with pytest.raises(ValueError):
                value(h, bad)
        with pytest.raises(ValueError):
            market_comparison(value(h, Assumptions()), 0.0)

    def test_a_non_positive_share_count_is_loud(self):
        h = plain_hist()
        h.shares_diluted = 0.0
        with pytest.raises(ValuationError, match="shares"):
            value(h, Assumptions())

    def test_fundamentals_validate(self):
        with pytest.raises(ValueError):
            YearData(2024.0, "2024-12-31")
        with pytest.raises(ValueError):
            YearData(2024, "2024-12-31", revenue=float("nan"))
        y = YearData(2024, "2024-12-31", income_before_tax=-5.0, income_tax_expense=1.0,
                     current_assets=500.0, current_liabilities=300.0, cash_and_equivalents=100.0)
        assert y.effective_tax_rate() is None and y.net_working_capital() == 100.0
        assert np.isclose(YearData(2024, "x", income_before_tax=100.0,
                                   income_tax_expense=21.0).effective_tax_rate(), 0.21)
