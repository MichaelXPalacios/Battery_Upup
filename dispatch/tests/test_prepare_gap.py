"""prepare_signal sizes its ingest look-back from the lake's gap (lead fix, 11 Oct 2026, after P1's live check failed closed on a
14-hour hole until a manual backfill)."""
import pandas as pd
import pytest

from dispatch.data import catalog, flows

NOW = pd.Timestamp("2026-10-11 18:00", tz="UTC")


@pytest.mark.parametrize("ages_h, expected", [
    ({}, flows.PREPARE_MIN_LOOKBACK_H),                                     # empty lake: the normal look-back
    ({"12301": 0.2}, flows.PREPARE_MIN_LOOKBACK_H),                          # fresh lake: unchanged behaviour
    ({"12301": 14.0, "13114": 1.0}, 14.0 + flows.PREPARE_GAP_MARGIN_H),       # P1's case: the oldest gap wins
    ({"12301": 400.0}, flows.PREPARE_MAX_LOOKBACK_H),                         # beyond ERCOT's public window: capped
])
def test_lookback_covers_the_gap(monkeypatch, ages_h, expected):
    monkeypatch.setattr(catalog, "freshness", lambda now, ids: {k: v * 3600 for k, v in ages_h.items()})
    assert flows.gap_lookback_h(["12301", "13114"], NOW) == pytest.approx(expected)


def test_unreadable_catalog_falls_back(monkeypatch):
    def boom(now, ids):
        raise OSError("catalog locked")
    monkeypatch.setattr(catalog, "freshness", boom)
    assert flows.gap_lookback_h(["12301"], NOW) == flows.PREPARE_MIN_LOOKBACK_H


def test_prepare_signal_passes_the_gap_to_the_ingest(monkeypatch):
    seen = {}
    monkeypatch.setattr(catalog, "freshness", lambda now, ids: {"12301": 14 * 3600})
    monkeypatch.setattr(flows, "_ingest_once", lambda ids, **kw: seen.update(kw) or {"ok": True})
    monkeypatch.setattr(flows, "_LAST_INGEST", {"t": -1e12})
    from dispatch.features import online
    monkeypatch.setattr(online, "refresh_latest", lambda sps, now, store=None, log=None: {})
    out = flows.prepare_signal(["HB_NORTH"], now_utc=NOW)
    assert seen["lookback_h"] == pytest.approx(14.5) and out["lookback_h"] == pytest.approx(14.5)
