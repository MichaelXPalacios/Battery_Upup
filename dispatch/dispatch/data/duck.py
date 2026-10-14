"""DuckDB over the parsed parquet: one view per dataset, unified history views, and an as-of join helper.

    con = duck.connect()
    con.sql("select max(interval_start_utc) from rt_spp").fetchall()
    duck.asof_join(con, decisions_df, "rt_spp_all", by=["settlement_point"], left_time="asof_utc",
                   right_cols=["price_usd_per_mwh"])

Views
* `<dataset>`   one per parsed dataset (rt_spp, dam_spp, dam_as, rt_as, load_forecast, sced_shadow, sced_lambda, sp_map,
                rt_spp_hist, dam_spp_hist, dam_as_hist, rt_as_hist, gas_hh, weather_obs[, weather_fcst]); empty with the right
                columns when nothing is parsed yet.
* `rt_spp_all`, `dam_spp_all`, `dam_as_all`, `rt_as_all`  the live window and the yearly history together, ONE row per key.
                Where both exist the REAL (MIS) row wins over the ASSUMPTION (archive) row. LZEW / LZ_DCEW price variants dropped.

Point in time: `published_utc <= asof` is the only rule (DESIGN.md). `asof_join` is DuckDB's ASOF JOIN on exactly that.
"""
from __future__ import annotations

from typing import Optional, Sequence, Union

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa

from . import parse

_ARROW_TO_SQL = {"timestamp[us, tz=UTC]": "TIMESTAMPTZ", "string": "VARCHAR", "double": "DOUBLE", "bool": "BOOLEAN", "int32": "INTEGER"}
EW_TYPES = ("LZEW", "LZ_DCEW")
# unified view -> (main dataset, history dataset, key columns)
UNIFIED = {
    "rt_spp_all": ("rt_spp", "rt_spp_hist", ("settlement_point", "interval_start_utc")),
    "dam_spp_all": ("dam_spp", "dam_spp_hist", ("settlement_point", "interval_start_utc")),
    "dam_as_all": ("dam_as", "dam_as_hist", ("product", "interval_start_utc")),
    "rt_as_all": ("rt_as", "rt_as_hist", ("product", "interval_start_utc")),
}


def _empty_select(schema: pa.Schema) -> str:
    cols = ", ".join(f"CAST(NULL AS {_ARROW_TO_SQL[str(f.type)]}) AS {f.name}" for f in schema)
    return f"SELECT {cols} WHERE FALSE"


def connect(database: str = ":memory:") -> duckdb.DuckDBPyConnection:
    """A DuckDB connection (UTC session) with a view for every parsed dataset and the unified history views."""
    con = duckdb.connect(database)
    con.execute("SET TimeZone='UTC'")
    for name, ds in parse.DATASETS.items():
        files = [p.as_posix() for p in parse.partition_files(name)]
        if files:
            lst = ", ".join("'" + f.replace("'", "''") + "'" for f in files)
            con.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet([{lst}], union_by_name=true, hive_partitioning=false)")
        else:
            con.execute(f"CREATE OR REPLACE VIEW {name} AS {_empty_select(ds.schema)}")
    for view, (main, hist, key) in UNIFIED.items():
        k = ", ".join(key)
        ew = f"WHERE sp_type NOT IN {EW_TYPES!r}" if "sp_type" in parse.DATASETS[main].schema.names else ""
        con.execute(f"""
            CREATE OR REPLACE VIEW {view} AS
            SELECT * EXCLUDE (_prio, _rk) FROM (
                SELECT *, row_number() OVER (PARTITION BY {k} ORDER BY _prio, published_utc) AS _rk FROM (
                    SELECT *, 0 AS _prio FROM {main} {ew}
                    UNION ALL BY NAME
                    SELECT *, 1 AS _prio FROM {hist} {ew}
                )
            ) WHERE _rk = 1""")
    return con


def asof_join(con: duckdb.DuckDBPyConnection, left: Union[str, pd.DataFrame], right: str, *, by: Sequence[str] = (),
              left_time: str = "asof_utc", right_time: str = "published_utc", right_cols: Optional[Sequence[str]] = None,
              how: str = "left") -> pd.DataFrame:
    """For each left row, attach the NEWEST right row with equal `by` keys and `right_time <= left_time`.

    This is the point-in-time join: with left_time = the decision time `asof_utc` and right_time = `published_utc`, a row
    published after the decision can never be attached. `left` is a DataFrame or the name of a table/view; `right` is the
    name of a table/view (e.g. 'dam_spp_all'). A DataFrame `left` comes back in its own row order. `right_cols` are the columns to bring over (default: all except the keys,
    prefixed `r_` when the name clashes). `how` is 'left' (keep unmatched left rows, NULLs) or 'inner'."""
    if how not in ("left", "inner"):
        raise ValueError("how must be 'left' or 'inner'")
    keep_order = isinstance(left, pd.DataFrame)
    if keep_order:
        left = left.reset_index(drop=True).assign(_asof_rn=np.arange(len(left)))
        con.register("_asof_left", left)
        lname = "_asof_left"
    else:
        lname = left
    rcols = list(right_cols) if right_cols else [c for c in con.sql(f"SELECT * FROM {right} LIMIT 0").columns if c not in by]
    left_cols = con.sql(f"SELECT * FROM {lname} LIMIT 0").columns
    select_r = ", ".join(f"r.{c} AS {('r_' + c) if c in left_cols else c}" for c in rcols)
    on = " AND ".join([f"l.{k} = r.{k}" for k in by] + [f"l.{left_time} >= r.{right_time}"])
    kind = "ASOF LEFT JOIN" if how == "left" else "ASOF JOIN"
    sql = f"SELECT l.*, {select_r} FROM {lname} AS l {kind} {right} AS r ON {on}" + (" ORDER BY l._asof_rn" if keep_order else "")
    try:
        out = con.sql(sql).df()
    finally:
        if keep_order:
            con.unregister("_asof_left")
    return out.drop(columns="_asof_rn") if keep_order else out
