"""Unit arithmetic in one place, so MW vs MWh slips fail tests instead of reaching a ledger.

Conventions: power in MW, energy in MWh, energy prices in $/MWh, ancillary prices in $/MW-h, money in $.
"""
from .config import DT_RT_H


def energy_mwh(power_mw, hours=DT_RT_H):
    """MW held for `hours` -> MWh."""
    return power_mw * hours


def energy_revenue_usd(price_usd_per_mwh, energy_mwh_):
    """$/MWh x MWh -> $. Pass energy, not power."""
    return price_usd_per_mwh * energy_mwh_


def interval_energy_revenue_usd(price_usd_per_mwh, power_mw, hours=DT_RT_H):
    """Revenue of holding `power_mw` (positive = discharge) for one interval at a $/MWh price."""
    return energy_revenue_usd(price_usd_per_mwh, energy_mwh(power_mw, hours))


def as_revenue_usd(mcpc_usd_per_mw_h, award_mw, hours=DT_RT_H):
    """Ancillary capacity: $/MW-h x MW x h -> $."""
    return mcpc_usd_per_mw_h * award_mw * hours


def usd_per_kw_yr(total_usd, power_mw, years):
    """Annualised value per kW of inverter power (the research arena's unit)."""
    return total_usd / (power_mw * 1000.0) / years
