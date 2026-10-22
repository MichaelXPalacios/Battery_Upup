"""Phase 1a commands (ERCOT market data), picked up automatically by run_grid.py.

  fetch-ercot  [REPORT_ID ... | all] [dry-run] [since=YYYY-MM-DD] [api start=YYYY-MM-DD end=YYYY-MM-DD]
  parse-ercot  [table ...]                 tables: see gridsite.ercot_parse.TABLES (default all)
  collect-daily                            fetch whatever is new in every MIS window, then parse incrementally
  report-ercot                             print the coverage / join / top-constraint / LMP-vs-lambda tables used in
                                           notes/phase1-market.md (reads the derived parquet; takes a few minutes)

Run from docs/research/grid with the research venv, e.g.
  ../research-data/.venv/Scripts/python.exe run_grid.py collect-daily

Nothing here schedules itself.  The MIS windows are short (NP6-787 and NP6-322 keep 5 days, NP6-86 / NP6-905 / the RT
AS prices 7 days, the DAM reports about a month), so collect-daily has to run at least every 4 days to avoid gaps.
"""
import datetime as dt

from . import ercot_parse, ercot_report, fetch_ercot
from .paths import ROOT

LOG = ROOT / "collect_daily.log"


def cmd_fetch_ercot(*args):
    return fetch_ercot.fetch_ercot(*args)


def cmd_parse_ercot(*args):
    failed = ercot_parse.parse_all(*args)
    if failed:
        raise SystemExit(f"{len(failed)} table(s) failed: {', '.join(failed)}")


def cmd_collect_daily(*args):
    """Fetch new files for every report, then rebuild only the parquet parts that changed.  Appends one line to
    research-data/grid/collect_daily.log.  Exits non-zero if any report or table failed."""
    started = dt.datetime.now(dt.timezone.utc)
    res = fetch_ercot.fetch_ercot("all", *args)
    fetch_failed = [r["report_id"] for r in res if r.get("failed")]
    parse_failed = ercot_parse.parse_all()
    cov = ercot_parse.coverage()
    line = (f"{started.isoformat(timespec='seconds')} files={sum(r.get('downloaded', 0) for r in res)} "
            f"mb={sum(r.get('mb', 0) for r in res):.1f} fetch_failed={fetch_failed} parse_failed={list(parse_failed)} "
            + " ".join(f"{r.table}={r.rows}" for r in cov.itertuples()))
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    if fetch_failed or parse_failed:
        raise SystemExit(1)


def cmd_report_ercot(*args):
    ercot_report.report()


COMMANDS = {"fetch-ercot": cmd_fetch_ercot, "parse-ercot": cmd_parse_ercot, "collect-daily": cmd_collect_daily,
            "report-ercot": cmd_report_ercot}
