"""Data flows: ingest, backfill, adopt, parse, rebuild (PRD Phase 0). Nothing here schedules itself or starts a server.

    python -m dispatch.data.flows ingest     # fetch what ERCOT posted recently, parse it, refresh the online features
    python -m dispatch.data.flows daily      # fetch NOAA weather (NCEI) and the Henry Hub price (EIA), parse them (run once a day)
    python -m dispatch.data.flows backfill   # fetch everything in the MIS windows and the free yearly archives
    python -m dispatch.data.flows adopt      # copy research-data (grid raw, zonal history, gas, weather) into the lake
    python -m dispatch.data.flows parse      # raw -> parsed parquet (incremental)
    python -m dispatch.data.flows features   # offline rt_v1 features for the default hubs
    python -m dispatch.data.flows online     # write the latest point-in-time features to Redis
    python -m dispatch.data.flows prepare    # ingest what is new, then refresh the online features (run before each POST /signal)
    python -m dispatch.data.flows weather    # one NWS hourly-forecast snapshot into the lake
    python -m dispatch.data.flows rebuild    # delete parsed/ and features/, rebuild both from raw (Gate 1 in production)

Prefect 3 is installed, but calling a Prefect flow with no API configured makes Prefect start its own temporary server
(measured: about 40 s and a local sqlite file), and the platform rule is "no Prefect server, nothing scheduled". So the five
functions below are plain Python by default. Set DISPATCH_FLOWS=prefect to get the SAME names as Prefect `@flow`s with `@task`
steps (use it against a Prefect server you run yourself); `--prefect` on the command line does the same for that one run.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from typing import Callable, Optional, Sequence

import pandas as pd
import pyarrow.parquet as pq

from .. import paths
from . import adopt as adopt_mod
from . import assumptions as A
from . import ercot, gas, parse, reports, weather
from .times import iso_z, utc

DEFAULT_SPS = ("HB_NORTH", "HB_WEST", "HB_HOUSTON", "HB_SOUTH", "HB_BUSAVG")     # the settlement points the platform forecasts first
DEFAULT_FEATURE_START = "2019-01-01"


def _log_print(s: str) -> None:
    print(s, flush=True)


# ------------------------------------------------------------------------------------------------ the five flows (plain)
def _datasets_for(report_ids: Sequence[str]) -> list:
    names = []
    for rid in report_ids:
        try:
            names += [d for d in reports.get(rid).datasets if d not in names]
        except KeyError:
            continue
    return names


def _ingest_once(report_ids: Optional[Sequence[str]] = None, *, lookback_h: float = 6.0, do_parse: bool = True,
                 refresh_online: bool = False, sps: Sequence[str] = DEFAULT_SPS, store=None, fetcher=None, daily: bool = False,
                 log: Callable[[str], None] = lambda s: None) -> dict:
    """One polling pass: fetch what ERCOT posted in the last `lookback_h` hours, parse the datasets that got new files, and
    optionally rebuild the online features. Returns a summary including the publish-to-store latency.

    `daily=True` also runs `ingest_daily` (NOAA weather and the Henry Hub price, each at most once per DAILY_FETCH_MIN_GAP_S in this process) and
    puts its summary under "daily". It is off by default: those two are not ERCOT reports, the served model does not read them, and a
    pass that runs every minute should not wait on NCEI or EIA. `fetcher` is the ERCOT fetcher only."""
    t0 = time.time()
    fetched = ercot.ingest_once(list(report_ids) if report_ids else reports.mis_ingest_ids(), fetcher=fetcher,
                                lookback=pd.Timedelta(hours=lookback_h), log=log)
    changed = [rid for rid, r in fetched.items() if r["downloaded"]]
    out = {"fetched": {rid: {k: r[k] for k in ("listed", "downloaded", "failed", "newest_published_utc")} for rid, r in fetched.items()},
           "parsed": {}, "online": {}}
    if do_parse and changed:
        for name in _datasets_for(changed):
            out["parsed"][name] = parse.parse_dataset(name, log=log)
    if daily:
        out["daily"] = ingest_daily(do_parse=do_parse, log=log)
    if refresh_online:
        from ..features import online
        out["online"] = online.refresh_latest(sps, store=store, log=log)
        newest = [utc(r["newest_published_utc"]) for r in fetched.values() if r["newest_published_utc"]]
        if newest:
            out["publish_to_store_s"] = round((pd.Timestamp.now(tz="UTC") - max(newest)).total_seconds(), 1)
    out["elapsed_s"] = round(time.time() - t0, 1)
    return out


_LAST_DAILY: dict = {}                     # source -> time.monotonic() of the last attempt in this process


def ingest_daily(*, now_utc=None, weather_on: bool = True, gas_on: bool = True, do_parse: bool = True, force: bool = False,
                 refetch_days: Optional[int] = None, min_gap_s: Optional[float] = None, weather_fetcher=None, gas_fetcher=None,
                 gas_min_rows: int = gas.MIN_ROWS, log: Callable[[str], None] = lambda s: None) -> dict:
    """Fetch the two datasets that are not ERCOT reports and parse what changed. Run it once a day (`python -m dispatch.data.flows daily`).

        weather  NOAA GHCN-Daily, 18 stations, NCEI Access Data Service -> raw ncei/GHCND -> dataset weather_obs   (data/weather.py)
        gas      EIA Henry Hub daily spot, history page             -> raw eia/RNGWHHD  -> dataset gas_hh          (data/gas.py)

    Each source is independent: one that fails is reported under its "error" and the other still runs. A source asked again within `min_gap_s`
    (default A.DAILY_FETCH_MIN_GAP_S) in the same process is skipped, so a loop cannot hammer NCEI or EIA; `force=True` ignores that. Parsing runs
    after every successful fetch (`parse_dataset` is incremental: a few seconds when nothing changed), so a fetch whose parse was cut short is
    finished by the next call. Re-running changes nothing: identical bytes are a no-op in the lake and the parse finds its inputs unchanged.
    Neither dataset feeds the served model, so a failure here is for the monitor to flag (alert only, GHCND/RNGWHHD in monitoring/checks.py)."""
    gap = A.DAILY_FETCH_MIN_GAP_S if min_gap_s is None else float(min_gap_s)
    t0 = time.time()
    out: dict = {}
    plan = []
    if weather_on:
        plan.append(("weather", "weather_obs", lambda: weather.fetch_observations(
            now_utc, fetcher=weather_fetcher, refetch_days=refetch_days, log=log)))
    if gas_on:
        plan.append(("gas", "gas_hh", lambda: gas.fetch_history(now_utc, fetcher=gas_fetcher, min_rows=gas_min_rows, log=log)))
    for name, dataset, run in plan:
        if not force and time.monotonic() - _LAST_DAILY.get(name, -1e9) < gap:
            out[name] = {"skipped": f"asked less than {gap:.0f} s ago in this process"}
            continue
        _LAST_DAILY[name] = time.monotonic()
        try:
            res = run()
        except Exception as e:                                         # a dead source must not stop the other one
            out[name] = {"error": f"{type(e).__name__}: {e}"}
            log(f"  daily {name}: FAILED {out[name]['error']}")
            continue
        out[name] = res
        if do_parse:
            try:
                out[name]["parsed"] = parse.parse_dataset(dataset, log=log)
            except Exception as e:
                out[name]["error"] = f"parse {dataset}: {type(e).__name__}: {e}"
    out["elapsed_s"] = round(time.time() - t0, 1)
    return out


def _backfill_all(report_ids: Optional[Sequence[str]] = None, *, include_archives: bool = True, do_parse: bool = True,
                  fetcher=None, log: Callable[[str], None] = lambda s: None) -> dict:
    """Fetch everything in the MIS windows (and the free yearly archives) that the lake does not have yet, then parse."""
    t0 = time.time()
    fetched = ercot.backfill_all(list(report_ids) if report_ids else reports.mis_ingest_ids(include_archives), fetcher=fetcher, log=log,
                                 include_archives=include_archives)
    out = {"fetched": {rid: {k: r[k] for k in ("listed", "selected", "already_have", "downloaded", "bytes", "failed", "newest_published_utc")}
                       for rid, r in fetched.items()}}
    if do_parse:
        out["parsed"] = parse_all(log=log, strict=False)
    out["elapsed_s"] = round(time.time() - t0, 1)
    return out


def _adopt_all(*, do_parse: bool = False, staging: bool = True, log: Callable[[str], None] = lambda s: None) -> dict:
    """Copy research-data into the lake (originals untouched): grid raw MIS zips, NP6-785-ER yearly zips, gas, weather, and
    (staging=True) D1b's staged 60-day disclosure zips (~2.7 GB)."""
    t0 = time.time()
    out = adopt_mod.adopt_all(log=log, staging=staging)
    if do_parse:
        out["parsed"] = parse_all(log=log, strict=False)
    out["elapsed_s"] = round(time.time() - t0, 1)
    return out


def _parse_all(datasets: Optional[Sequence[str]] = None, *, force: bool = False, strict: bool = True, workers: int = 2,
               log: Callable[[str], None] = lambda s: None) -> dict:
    """Raw lake -> parsed parquet for every dataset (incremental: unchanged partitions are skipped)."""
    return parse.parse_all(datasets, force=force, strict=strict, log=log, workers=workers)


def _rebuild_all(*, sps: Optional[Sequence[str]] = None, start: str = DEFAULT_FEATURE_START, end=None, decision_every: str = "4h",
                 features: bool = True, log: Callable[[str], None] = lambda s: None) -> dict:
    """Delete lake/parsed/ and features/ and rebuild both from the raw lake alone (Gate 1, run for real). Raw is never touched."""
    t0 = time.time()
    parse.clear_parsed()
    if paths.FEATURES.exists():
        shutil.rmtree(paths.FEATURES)
    out = {"parsed": parse.parse_all(log=log, strict=True)}
    if features:
        out["features"] = build_features(sps or DEFAULT_SPS, start, end, decision_every, log=log)
    out["elapsed_s"] = round(time.time() - t0, 1)
    return out


# ------------------------------------------------------------------------------------------------ helpers around the flows
def build_features(sps: Sequence[str] = DEFAULT_SPS, start: str = DEFAULT_FEATURE_START, end=None, decision_every: str = "4h",
                   log: Callable[[str], None] = lambda s: None, offset: str = "0min") -> dict:
    """Write the offline rt_v1 features of each settlement point for decisions in [start, end) (default end: the newest RT
    price published). Returns {sp: {'files': n, 'rows': n}}."""
    from ..features import build
    end_ts = utc(end) if end is not None else _latest_rt_published()
    start_ts = pd.Timestamp(start, tz="UTC") if isinstance(start, str) else utc(start)
    out = {}
    for sp in sps:
        try:
            files = build.write_offline(sp, start_ts, end_ts, decision_every, offset=offset, log=log)
            rows = sum(pq.read_metadata(f).num_rows for f in files)
            out[sp] = {"files": len(files), "rows": rows, "first": files[0].name if files else None, "last": files[-1].name if files else None}
        except Exception as e:
            out[sp] = {"error": f"{type(e).__name__}: {e}"}
            log(f"  features {sp}: FAILED {out[sp]['error']}")
    return out


def _latest_rt_published() -> pd.Timestamp:
    from . import catalog
    t = catalog.newest_published("NP6-905-CD")
    return t if t is not None else pd.Timestamp.now(tz="UTC")


def refresh_online(sps: Sequence[str] = DEFAULT_SPS, asof=None, *, store=None, log: Callable[[str], None] = lambda s: None) -> dict:
    """Build point-in-time features at `asof` (default: now) and write them to the online store (Redis)."""
    from ..features import online
    return online.refresh_latest(sps, asof, store=store, log=log)


FEATURE_REPORTS = ("NP6-905-CD", "NP4-190-CD", "NP4-188-CD", "NP3-565-CD")     # the live reports rt_v1 reads
_LAST_INGEST = {"t": -1e9}


# Lead, 11 Oct 2026 (P1's live check): a lake left idle for 14 h made the first signal fail closed (rt_mean_24h null) until a manual
# backfill, because prepare_signal only looked 3 h back. The look-back now covers the lake's actual gap, capped by what ERCOT's
# public MIS windows still hold, so an idle lake heals itself on the next signal.
PREPARE_MIN_LOOKBACK_H = 3.0        # ASSUMPTION: the normal per-signal look-back (unchanged from D0's value)
PREPARE_GAP_MARGIN_H = 0.5          # ASSUMPTION: extra look-back beyond the newest file, for files posted late
PREPARE_MAX_LOOKBACK_H = 5 * 24.0   # REAL (phase1-market.md): the shortest live MIS window (bus LMPs, SCED lambda) is about 5 days


def gap_lookback_h(report_ids: Sequence[str], now_utc=None) -> float:
    """Hours to look back so the ingest covers the lake's gap: max(3 h, oldest newest-file age + margin), capped at 5 days."""
    from . import catalog                                          # local import, as elsewhere in this module
    try:
        ages = catalog.freshness(now_utc, report_ids)
    except Exception:                                              # an unreadable catalog must not block the refresh
        return PREPARE_MIN_LOOKBACK_H
    worst_h = max((a / 3600.0 for a in ages.values()), default=0.0)
    return float(min(PREPARE_MAX_LOOKBACK_H, max(PREPARE_MIN_LOOKBACK_H, worst_h + PREPARE_GAP_MARGIN_H)))


def prepare_signal(sps: Sequence[str] = DEFAULT_SPS, *, now_utc=None, ingest: bool = True, min_ingest_gap_s: float = 20.0,
                   report_ids: Optional[Sequence[str]] = None, store=None, fetcher=None, log: Callable[[str], None] = lambda s: None) -> dict:
    """Everything that must happen RIGHT BEFORE a `POST /signal`: pull what ERCOT just posted, parse it, and rebuild the online
    features at the decision time, so that the stored frame's first row is the interval containing "now" and its newest input is as
    fresh as ERCOT's publication allows.

        flows.prepare_signal([spec.settlement_point])      # then POST /signal

    Who calls it: whatever triggers the signal (D2's scheduler, a cron job, the operator's loop), once per cycle per settlement point.
    `ingest=False` skips the fetch (replays, tests); an ingest less than `min_ingest_gap_s` after the previous one is skipped so a
    burst of signals does not hammer ERCOT. A failed fetch never blocks the refresh: the frame is built from what the lake has, and
    D2's age gate (now - max_published_utc) fails closed if that is too old. Returns {'ingest': ..., 'online': {sp: ...}, 'elapsed_s'}.

    `report_ids` defaults to EVERY live report (the eight in `reports.INGEST_IDS`), not only the four the features read
    (`FEATURE_REPORTS`): D2's stale-data monitor also watches NP6-331-CD, NP6-322-CD and NP6-86-CD, and a deployment that only calls
    this function would otherwise let those three age until the monitor halts dispatch. Even so, NP6-322-CD posts every 5 minutes
    and D2's allowance for it is 10: a loop that runs `ingest --online` every minute is still the way to keep the monitor quiet."""
    from ..features import online
    t0 = time.time()
    out: dict = {}
    if ingest and time.monotonic() - _LAST_INGEST["t"] >= min_ingest_gap_s:
        try:
            ids = list(report_ids) if report_ids else list(reports.INGEST_IDS)
            lookback_h = gap_lookback_h(ids, now_utc)
            out["lookback_h"] = lookback_h
            out["ingest"] = _ingest_once(ids, lookback_h=lookback_h, do_parse=True, fetcher=fetcher, log=log)
        except Exception as e:                                     # network trouble must not stop the refresh
            out["ingest"] = {"error": f"{type(e).__name__}: {e}"}
        _LAST_INGEST["t"] = time.monotonic()
    elif ingest:
        out["ingest"] = {"skipped": f"previous ingest less than {min_ingest_gap_s:.0f} s ago"}
    out["online"] = online.refresh_latest(list(sps), now_utc, store=store, log=log)
    out["elapsed_s"] = round(time.time() - t0, 2)
    return out


def weather_snapshot(log: Callable[[str], None] = lambda s: None) -> dict:
    """One NWS hourly forecast snapshot per station into the lake, then parse it."""
    out = weather.snapshot_forecasts(log=log)
    out["parsed"] = parse.parse_dataset("weather_fcst")
    return out


# ------------------------------------------------------------------------------------------------ Prefect wrappers (opt-in)
def _wrap_prefect(namespace: dict) -> bool:
    """Replace the five plain functions in `namespace` by Prefect flows of the same names. Returns True on success."""
    try:
        from prefect import flow, task
    except Exception as e:                      # pragma: no cover - depends on the environment
        print(f"[dispatch.data.flows] Prefect not importable ({type(e).__name__}); using plain functions", file=sys.stderr)
        return False
    fetch_task = task(name="backfill-one-report", retries=2, retry_delay_seconds=30)(ercot.backfill)
    parse_task = task(name="parse-dataset", retries=1)(parse.parse_dataset)

    @flow(name="dispatch-ingest-once")
    def ingest_once(report_ids=None, **kw):
        lookback = pd.Timedelta(hours=kw.pop("lookback_h", 6.0))
        ids = list(report_ids or reports.mis_ingest_ids())
        now = pd.Timestamp.now(tz="UTC")
        got = {rid: fetch_task(rid, since=now - lookback) for rid in ids}
        for name in _datasets_for([r for r, v in got.items() if v["downloaded"]]):
            parse_task(name)
        return got

    @flow(name="dispatch-backfill")
    def backfill_all(report_ids=None, **kw):
        ids = list(report_ids or reports.mis_ingest_ids(include_archives=True))
        got = {rid: fetch_task(rid) for rid in ids}
        for name in parse.DATASETS:
            parse_task(name)
        return got

    @flow(name="dispatch-adopt")
    def adopt_all(**kw):
        return _adopt_all(**kw)

    @flow(name="dispatch-parse")
    def parse_all(datasets=None, **kw):
        return _parse_all(datasets, **kw)

    @flow(name="dispatch-rebuild")
    def rebuild_all(**kw):
        return _rebuild_all(**kw)

    namespace.update(ingest_once=ingest_once, backfill_all=backfill_all, adopt_all=adopt_all, parse_all=parse_all,
                     rebuild_all=rebuild_all)
    return True


ingest_once, backfill_all, adopt_all, parse_all, rebuild_all = _ingest_once, _backfill_all, _adopt_all, _parse_all, _rebuild_all
PREFECT = False
if os.environ.get("DISPATCH_FLOWS", "plain").strip().lower() == "prefect":
    PREFECT = _wrap_prefect(globals())


# ------------------------------------------------------------------------------------------------ command line
def _has_errors(obj) -> bool:
    """True if a flow result carries a non-empty 'errors' / 'error' / 'failed' entry anywhere."""
    if isinstance(obj, dict):
        return any((k in ("errors", "error", "failed") and bool(v)) or _has_errors(v) for k, v in obj.items())
    return False


def _csv_list(s: Optional[str]) -> Optional[list]:
    return [x.strip() for x in s.split(",") if x.strip()] if s else None


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m dispatch.data.flows", description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=["ingest", "daily", "backfill", "adopt", "parse", "features", "online", "prepare", "weather", "rebuild"])
    ap.add_argument("--reports", help="comma-separated report ids (default: all that apply)")
    ap.add_argument("--sps", help=f"comma-separated settlement points (default: {','.join(DEFAULT_SPS)})")
    ap.add_argument("--start", default=DEFAULT_FEATURE_START, help="first decision date for features (default %(default)s)")
    ap.add_argument("--end", help="end of the feature window (default: newest RT price)")
    ap.add_argument("--every", default="4h", help="decision cadence for features (default %(default)s)")
    ap.add_argument("--offset", default="0min", help="shift of the decision grid for features, e.g. 7min30s (default %(default)s)")
    ap.add_argument("--force", action="store_true", help="parse: rebuild even unchanged partitions")
    ap.add_argument("--no-parse", action="store_true", help="ingest/backfill: do not parse after fetching")
    ap.add_argument("--online", action="store_true", help="ingest: also refresh the online features")
    ap.add_argument("--daily", action="store_true", help="ingest: also run the daily weather and gas fetch")
    ap.add_argument("--refetch-days", type=int, help="daily: days before the newest weather day to ask NCEI for again (default %d)" % A.WEATHER_FETCH_REFETCH_DAYS)
    ap.add_argument("--prefect", action="store_true", help="run through Prefect flows (starts a temporary Prefect server unless PREFECT_API_URL is set)")
    a = ap.parse_args(argv)
    if a.prefect and not PREFECT:
        os.environ["DISPATCH_FLOWS"] = "prefect"
        _wrap_prefect(globals())
    reps, sps = _csv_list(a.reports), _csv_list(a.sps) or list(DEFAULT_SPS)
    g = globals()
    log = _log_print
    if a.command == "ingest":
        res = g["ingest_once"](reps, do_parse=not a.no_parse, refresh_online=a.online, sps=sps, daily=a.daily, log=log) if not (a.prefect or PREFECT) else g["ingest_once"](reps)
    elif a.command == "daily":
        res = ingest_daily(do_parse=not a.no_parse, force=True, refetch_days=a.refetch_days, log=log)
    elif a.command == "backfill":
        res = g["backfill_all"](reps, do_parse=not a.no_parse, log=log) if not (a.prefect or PREFECT) else g["backfill_all"](reps)
    elif a.command == "adopt":
        res = g["adopt_all"](log=log)
    elif a.command == "parse":
        res = g["parse_all"](_csv_list(a.reports), force=a.force, log=log)
    elif a.command == "features":
        res = build_features(sps, a.start, a.end, a.every, log=log, offset=a.offset)
    elif a.command == "online":
        res = refresh_online(sps, log=log)
    elif a.command == "prepare":
        res = prepare_signal(sps, ingest=not a.no_parse, report_ids=reps, log=log)
    elif a.command == "weather":
        res = weather_snapshot(log=log)
    else:
        res = g["rebuild_all"](sps=sps, start=a.start, end=a.end, decision_every=a.every, log=log)
    print(json.dumps(res, indent=1, default=str)[:20000])
    return 1 if _has_errors(res) else 0


if __name__ == "__main__":
    raise SystemExit(main())
