"""The forecast model's mathematical guarantees (§15, §16).

These are properties the model must hold for *any* input series, checked
against real behaviour rather than a fixed expected number.
"""
from __future__ import annotations

import math
from datetime import date

import pytest

from app.forecast import model as fmodel


def _series(months: int, monthly_growth: float, start: float = 100.0):
    """A clean exponential series — a controlled input for testing invariants,
    not a substitute for real data (the real backtest is ml/backtest_forecast.py)."""
    periods, values = [], []
    for i in range(months):
        periods.append(date(1995 + i // 12, i % 12 + 1, 1))
        values.append(start * math.exp(monthly_growth * i))
    return periods, values


class TestDamping:
    def test_momentum_sum_converges(self):
        """Momentum must not extrapolate linearly, or long horizons explode."""
        values = [fmodel._damped_momentum_sum(h) for h in (12, 60, 120, 600, 6000)]
        assert values == sorted(values), "must be monotonically increasing"
        # Converges to phi/(1-phi); far horizons must be nearly identical.
        assert abs(values[-1] - values[-2]) < 1e-6
        assert values[-1] < 20, "must stay bounded"

    def test_undamped_would_be_linear(self):
        assert fmodel._damped_momentum_sum(50, phi=1.0) == 50.0


class TestFit:
    def test_refuses_a_series_that_is_too_short(self):
        periods, values = _series(fmodel.MIN_MONTHS_REQUIRED - 1, 0.002)
        assert fmodel.fit(
            periods=periods, index_values=values, area_code="X",
            area_name="X", area_level="local_authority", segment="all",
        ) is None

    def test_recovers_a_known_drift(self):
        periods, values = _series(300, 0.003)
        fitted = fmodel.fit(
            periods=periods, index_values=values, area_code="X",
            area_name="X", area_level="local_authority", segment="all",
        )
        assert fitted is not None
        assert fitted.g_long == pytest.approx(0.003, abs=1e-6)
        # With no national series supplied there is nothing to shrink toward.
        assert fitted.shrinkage_weight == 0.0
        assert fitted.g_used_long == pytest.approx(0.003, abs=1e-6)

    def test_shrinks_toward_the_national_series(self):
        periods, values = _series(120, 0.006)           # hot local market
        _np, nat = _series(300, 0.001)                  # cool national market
        fitted = fmodel.fit(
            periods=periods, index_values=values, area_code="X",
            area_name="X", area_level="local_authority", segment="all",
            national_values=nat,
        )
        assert fitted is not None
        assert fitted.shrinkage_weight > 0
        # The applied drift must sit between local and national.
        assert 0.001 < fitted.g_used_long < 0.006


class TestProject:
    def _fitted(self, growth=0.004, months=300):
        periods, values = _series(months, growth)
        return fmodel.fit(
            periods=periods, index_values=values, area_code="X",
            area_name="X", area_level="local_authority", segment="all",
        )

    def test_uncertainty_grows_with_horizon(self):
        """§16: 2027 narrow, 2030 wider, 2035 significantly wider."""
        fitted = self._fitted()
        projections = fmodel.project(fitted, [12, 48, 84, 120])
        widths = [math.log(p.ratio_high / p.ratio_low) for p in projections]
        assert widths == sorted(widths), "intervals must widen with horizon"
        assert widths[-1] > widths[0] * 1.5

    def test_interval_contains_the_point_estimate(self):
        fitted = self._fitted()
        for p in fmodel.project(fitted, [12, 60, 120]):
            assert p.ratio_low < p.ratio < p.ratio_high

    def test_zero_horizon_is_the_identity(self):
        p = fmodel.project(self._fitted(), [0])[0]
        assert p.ratio == 1.0

    def test_drift_shrinkage_is_applied(self):
        """The calibrated shrinkage must actually reduce the projection."""
        fitted = self._fitted(growth=0.004)
        shrunk = fmodel.project(fitted, [120])[0].ratio
        full = fmodel.project(
            fitted, [120], drift_shrinkage=1.0, momentum_shrinkage=0.0
        )[0].ratio
        assert shrunk < full
        assert fmodel.DRIFT_SHRINKAGE < 1.0

    def test_momentum_is_disabled_by_calibration(self):
        """Measurement set the momentum weight to zero; that must be in effect."""
        assert fmodel.MOMENTUM_SHRINKAGE == 0.0
        # A hot recent spell must therefore not change the projection.
        periods, values = _series(200, 0.002)
        for i in range(len(values) - 24, len(values)):
            values[i] *= 1.10                       # a recent surge
        fitted = fmodel.fit(
            periods=periods, index_values=values, area_code="X",
            area_name="X", area_level="local_authority", segment="all",
        )
        assert fitted is not None
        assert fitted.momentum != 0, "the fit should still measure momentum"
        with_momentum = fmodel.project(
            fitted, [60], momentum_shrinkage=1.0
        )[0].ratio
        without = fmodel.project(fitted, [60])[0].ratio
        assert with_momentum != without, "the parameter must be wired through"

    def test_backtest_sigma_is_used_when_available(self):
        fitted = self._fitted()
        table = {12: 0.05, 60: 0.15, 120: 0.25}
        provisional = fmodel.project(fitted, [60])[0]
        calibrated = fmodel.project(fitted, [60], sigma_by_horizon=table)[0]
        assert provisional.interval_basis == "provisional"
        assert calibrated.interval_basis == "backtest"
        assert calibrated.sigma_log == pytest.approx(0.15)

    def test_sigma_interpolation_and_extrapolation(self):
        table = {12: 0.06, 60: 0.16}
        assert fmodel._interpolate_sigma(table, 12) == 0.06
        assert 0.06 < fmodel._interpolate_sigma(table, 36) < 0.16
        # Beyond the table, sqrt-time extrapolation — larger, but not absurd.
        far = fmodel._interpolate_sigma(table, 240)
        assert far > 0.16 and far < 0.16 * 3


class TestMonthArithmetic:
    def test_add_months_rolls_the_year(self):
        assert fmodel._add_months(date(2026, 11, 1), 3) == date(2027, 2, 1)
        assert fmodel._add_months(date(2026, 1, 1), 12) == date(2027, 1, 1)

    def test_months_between_is_signed_and_consistent(self):
        assert fmodel.months_between(date(2026, 1, 1), date(2027, 1, 1)) == 12
        assert fmodel.months_between(date(2027, 1, 1), date(2026, 1, 1)) == -12
