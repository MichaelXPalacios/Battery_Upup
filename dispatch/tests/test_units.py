"""Regression tests for unit slips (PRD NFR Testing: 'unit regression tests for slips like MW vs MWh')."""
import math

import pytest

from dispatch import units
from dispatch.contracts import BatterySpec


def test_quarter_hour_discharge_revenue():
    # 1 MW for one 15-minute interval at $100/MWh is 0.25 MWh -> $25, not $100
    assert units.interval_energy_revenue_usd(100.0, 1.0) == pytest.approx(25.0)
    assert units.energy_mwh(1.0) == pytest.approx(0.25)


def test_as_revenue_is_mw_times_hours():
    # $2/MW-h on a 10 MW award for one 15-minute interval -> $5
    assert units.as_revenue_usd(2.0, 10.0) == pytest.approx(5.0)


def test_usd_per_kw_yr():
    # $294k over one year on a 10 MW site -> $29.4/kW-yr (the PRD's 2025 fleet figure)
    assert units.usd_per_kw_yr(294_000, 10.0, 1.0) == pytest.approx(29.4)


def test_spec_loss_conventions():
    a = BatterySpec("a", "HB_NORTH", 1.0, 2.0, rte=0.81, loss_side="charge")
    b = BatterySpec("b", "HB_NORTH", 1.0, 2.0, rte=0.81, loss_side="split")
    assert a.eta_charge * a.eta_discharge == pytest.approx(0.81)
    assert b.eta_charge == pytest.approx(0.9) and b.eta_discharge == pytest.approx(0.9)
    assert a.duration_h == 2.0


@pytest.mark.parametrize("kw", [dict(power_mw=0), dict(energy_mwh=-1), dict(rte=1.2), dict(loss_side="x"),
                                dict(soc_min_mwh=3.0), dict(as_products=("FOO",))])
def test_spec_rejects_bad_inputs(kw):
    base = dict(asset_id="a", settlement_point="HB_NORTH", power_mw=1.0, energy_mwh=2.0)
    base.update(kw)
    with pytest.raises(ValueError):
        BatterySpec(**base)
