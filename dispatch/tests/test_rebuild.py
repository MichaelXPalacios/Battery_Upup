"""GATE 1 (PRD Phase 0): features rebuild exactly from raw.

Copy a small REAL subset of MIS zips from research-data/grid/raw/ercot into a temp lake, parse them and build features, hash
every parquet partition, delete `parsed/` and `features/`, rebuild, and require identical sha256 for every file. The same
rebuild is repeated in a fresh Python process with a different hash seed. Around that: parser unit tests (DST days, the
publish-time rule of each dataset) and the xlsx reader, which feeds the yearly archives.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from dispatch import paths as paths_mod
from dispatch.data import adopt, assumptions as A, lake, parse, xlsx
from dispatch.data.times import parse_mis_publish
from dispatch.features import build, sets

RAW = paths_mod.GRID_RAW_ERCOT
RESEARCH = paths_mod.RESEARCH_DATA
PROJECT = Path(__file__).resolve().parents[1]
SP_LIST = ("HB_NORTH", "HB_BUSAVG")
DECISIONS = (pd.Timestamp("2026-10-09T00:00:00Z"), pd.Timestamp("2026-10-09T21:00:00Z"))


@pytest.fixture(autouse=True)
def _close_lake_handles():
    yield
    lake.close_all()


# ------------------------------------------------------------------------------------------------ helpers
def _pick(report_id: str, day_prefixes, limit=None) -> list[Path]:
    """Real MIS zips of a report whose file-name posting date (YYYYMMDD) is one of `day_prefixes`."""
    d = RAW / report_id
    files = sorted(p for p in d.glob("*.zip") if any(f".{x}." in p.name for x in day_prefixes))
    return files[:limit] if limit else files


def _need_real_data(*dirs):
    for r in dirs:
        if not (RAW / r).exists() or not list((RAW / r).glob("*.zip")):
            pytest.skip(f"research-data/grid/raw/ercot/{r} is not available on this machine")


def copy_real_subset() -> dict:
    """Copy (never move) a small real subset into the current lake with origin adopt:<path>."""
    _need_real_data("NP6-905-CD", "NP4-190-CD", "NP4-188-CD", "NP6-331-CD", "NP6-322-CD", "NP6-86-CD", "NP4-160-SG")
    plan = {
        "NP6-905-CD": _pick("NP6-905-CD", ["20261007", "20261008", "20261009"]),          # 3 days of 15-minute prices at every point
        "NP4-190-CD": _pick("NP4-190-CD", ["20261006", "20261007", "20261008", "20261009"]),
        "NP4-188-CD": _pick("NP4-188-CD", ["20261006", "20261007", "20261008", "20261009"]),
        "NP6-331-CD": _pick("NP6-331-CD", ["20261008"]),
        "NP6-322-CD": _pick("NP6-322-CD", ["20261009"], limit=60),
        "NP6-86-CD": _pick("NP6-86-CD", ["20261009"]),
        "NP4-160-SG": [p for p in sorted((RAW / "NP4-160-SG").glob("*.zip"))][:1],
    }
    n = 0
    for rid, files in plan.items():
        for f in files:
            lake.put("ercot", rid, parse_mis_publish(f.name), f.name, f, f"adopt:research-data/grid/raw/ercot/{rid}/{f.name}")
            n += 1
    # Henry Hub and one weather station: the real files, trimmed to the last two years (fewer monthly partitions to write);
    # the copy keeps the original file's modification time, which is what the lake file name records as the fetch time.
    src_dir = paths_mod.HOME / "_src_subset"
    src_dir.mkdir(parents=True, exist_ok=True)
    for src in (RESEARCH / "gas" / "DHHNGSP.csv", RESEARCH / "weather" / "USW00003927.csv"):
        if not src.exists():
            continue
        df = pd.read_csv(src, dtype=str, keep_default_na=False)
        df = df[df["DATE"] >= "2025-01-01"]
        dst = src_dir / src.name
        df.to_csv(dst, index=False, quoting=1 if src.name.startswith("USW") else 0)
        st = src.stat()
        os.utime(dst, (st.st_atime, st.st_mtime))
        asof = adopt._mtime_utc(dst)
        name = f"{dst.stem}.asof_{adopt.asof_tag(asof)}{dst.suffix}"
        if src.name.startswith("DHHNGSP"):
            lake.put("fred", "DHHNGSP", asof, name, dst, adopt._origin(src))
        else:
            lake.put("ncei", "GHCND", asof, name, dst, adopt._origin(src))
    assert all(plan.values()) and n >= 300, {k: len(v) for k, v in plan.items()}
    return {rid: len(f) for rid, f in plan.items()}


def hash_tree(*roots: Path) -> dict[str, str]:
    out = {}
    for root in roots:
        for p in sorted(root.rglob("*.parquet")):
            out[p.relative_to(root.parent).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def build_all() -> dict:
    parse.parse_all(strict=True)
    for sp in SP_LIST:
        build.write_offline(sp, DECISIONS[0], DECISIONS[1], "3h")


@pytest.fixture(scope="module")
def seed_home(tmp_path_factory):
    """A lake built once from the real subset (raw + parsed + features) and its file hashes. Tests that only read or that
    rebuild copy it into their own DISPATCH_HOME, so the expensive parse happens once."""
    import importlib
    from dispatch import paths
    home = tmp_path_factory.mktemp("seed") / "home"
    old = os.environ.get("DISPATCH_HOME")
    os.environ["DISPATCH_HOME"] = str(home)
    importlib.reload(paths)
    try:
        paths.ensure()
        copy_real_subset()
        build_all()
        first = hash_tree(paths.PARSED, paths.FEATURES)
        lake.close_all()
    finally:
        if old is None:
            os.environ.pop("DISPATCH_HOME", None)
        else:
            os.environ["DISPATCH_HOME"] = old
        importlib.reload(paths)
    return home, first


@pytest.fixture()
def seeded(dispatch_home, seed_home):
    home, first = seed_home
    shutil.copytree(home, dispatch_home.HOME, dirs_exist_ok=True)
    return first


# ------------------------------------------------------------------------------------------------ GATE 1
def test_gate1_features_rebuild_exactly_from_raw(dispatch_home):
    subset = copy_real_subset()
    raw_before = {e["path"]: e["sha256"] for e in lake.catalog_entries()}
    build_all()
    first = hash_tree(dispatch_home.PARSED, dispatch_home.FEATURES)

    parsed_names = {k.split("/")[1] for k in first if k.startswith("parsed/")}
    assert {"rt_spp", "dam_spp", "dam_as", "rt_as", "sced_lambda", "sced_shadow", "sp_map", "gas_hh", "weather_obs"} <= parsed_names, parsed_names
    feat_files = [k for k in first if k.startswith("features/")]
    assert len(feat_files) == len(SP_LIST) and len(first) >= 20, (len(first), subset)
    for sp in SP_LIST:
        X, y, dec = build.read_offline(sp)
        assert len(X) > 300 and X["rt_last"].notna().all() and (X["max_published_utc"] <= X["asof_utc"]).all()
        assert X["asof_utc"].nunique() == 7 and y.notna().all() and "sp" not in X.columns
        assert X["dam_spp"].notna().mean() > 0.5 and X["gas_hh"].notna().all()

    shutil.rmtree(dispatch_home.PARSED)                                  # the gate: delete parsed/ and features/ ...
    shutil.rmtree(dispatch_home.FEATURES)
    assert not dispatch_home.PARSED.exists() and not dispatch_home.FEATURES.exists()
    build_all()                                                          # ... rebuild from the raw lake alone ...
    second = hash_tree(dispatch_home.PARSED, dispatch_home.FEATURES)

    assert second == first, [k for k in set(first) | set(second) if first.get(k) != second.get(k)]   # ... and every sha256 matches
    assert {e["path"]: e["sha256"] for e in lake.catalog_entries()} == raw_before               # raw untouched
    assert lake.verify(check_hash=True) == []


def test_gate1_rebuild_in_a_fresh_process_gives_the_same_hashes(dispatch_home, seeded):
    """Different interpreter, different PYTHONHASHSEED, different thread timing: same bytes."""
    first = seeded
    assert hash_tree(dispatch_home.PARSED, dispatch_home.FEATURES) == first            # the copy is faithful
    shutil.rmtree(dispatch_home.PARSED)
    shutil.rmtree(dispatch_home.FEATURES)
    code = chr(10).join([
        "import json, hashlib",
        "import pandas as pd",
        "from dispatch import paths",
        "from dispatch.data import parse",
        "from dispatch.features import build",
        "parse.parse_all(strict=True)",
        f"for sp in {SP_LIST!r}:",
        f"    build.write_offline(sp, pd.Timestamp('{DECISIONS[0].isoformat()}'), pd.Timestamp('{DECISIONS[1].isoformat()}'), '3h')",
        "out = {}",
        "for root in (paths.PARSED, paths.FEATURES):",
        "    for p in sorted(root.rglob('*.parquet')):",
        "        out[p.relative_to(root.parent).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()",
        "print('HASHES=' + json.dumps(out))"])
    env = {**os.environ, "DISPATCH_HOME": str(dispatch_home.HOME), "PYTHONHASHSEED": "12345", "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, %r); exec(%r)" % (str(PROJECT), code)],
                       capture_output=True, text=True, env=env, cwd=str(PROJECT), timeout=900)
    assert r.returncode == 0, r.stderr[-2000:]
    second = json.loads(next(l for l in r.stdout.splitlines() if l.startswith("HASHES="))[len("HASHES="):])
    assert second == first


def test_parse_is_incremental_and_idempotent(dispatch_home, seeded):
    before = hash_tree(dispatch_home.PARSED)
    r2 = parse.parse_all(strict=True)
    assert all(v["written"] == 0 for v in r2.values()), {k: v["written"] for k, v in r2.items()}
    assert r2["rt_spp"]["skipped"] == 3 and hash_tree(dispatch_home.PARSED) == before
    # a new raw file for an existing day rewrites only that day's partition
    new = RAW / "NP6-905-CD"
    extra = next(p for p in sorted(new.glob("*.zip")) if ".20261010." in p.name and "_csv" in p.name)
    lake.put("ercot", "NP6-905-CD", parse_mis_publish(extra.name), extra.name, extra, "adopt:test")
    r3 = parse.parse_all(["rt_spp"], strict=True)
    assert r3["rt_spp"]["written"] == 1 and r3["rt_spp"]["skipped"] == 3
    after = hash_tree(dispatch_home.PARSED)
    changed = {k for k in after if before.get(k) != after[k]}
    assert len(changed) == 1 and next(iter(changed)).startswith("parsed/rt_spp/date=2026-10-10")


def test_parsed_rows_carry_the_contract_columns_and_real_publish_times(dispatch_home, seeded):
    for name in ("rt_spp", "dam_spp", "dam_as", "rt_as", "sced_lambda", "sced_shadow"):
        df = parse.read(name)
        assert len(df) > 0
        for c in ("published_utc", "interval_start_utc"):
            assert str(df[c].dtype) == "datetime64[us, UTC]", (name, c)
        assert (df["pub_basis"] == "REAL").all() and df["raw_file"].str.startswith("cdr.").all()
    rt = parse.read("rt_spp")
    f = next(p for p in sorted((RAW / "NP6-905-CD").glob("*.zip")) if "20261008.101702" in p.name or ".20261008.1017" in p.name)
    assert (rt[rt["raw_file"] == f.name]["published_utc"] == parse_mis_publish(f.name)).all()
    # the file posted 10:17 CPT holds the interval that ended at 10:15: its start is 10:00 CPT = 15:00 UTC
    one = rt[rt["raw_file"] == f.name]
    assert one["interval_start_utc"].nunique() == 1 and one["interval_start_utc"].iloc[0].hour == 15 and one["interval_start_utc"].iloc[0].minute == 0
    assert (one["published_utc"].iloc[0] - one["interval_start_utc"].iloc[0]) == pd.Timedelta(minutes=17, seconds=2, milliseconds=135) \
        or (one["published_utc"].iloc[0] - one["interval_start_utc"].iloc[0]) < pd.Timedelta(minutes=20)
    dam = parse.read("dam_spp")
    assert (dam["published_utc"].dt.tz_convert("America/Chicago").dt.hour.between(12, 14)).all()      # DAM posts around 13:00 CPT
    assert (dam["interval_start_utc"] > dam["published_utc"]).all()                                  # for tomorrow


def test_assumed_publish_times_are_labelled_and_conservative(dispatch_home, seeded):
    gas = parse.read("gas_hh")
    assert (gas["pub_basis"] == "ASSUMPTION").all()
    lag = gas["published_utc"] - gas["interval_start_utc"]
    fetched = adopt.asof_from_name(gas["raw_file"].iloc[0])
    assert (gas["published_utc"] <= fetched).all()                                                   # never after the fetch of its file
    old = gas[gas["interval_start_utc"] < pd.Timestamp("2026-08-01", tz="UTC")]
    assert ((old["published_utc"] - old["interval_start_utc"]).between(pd.Timedelta(days=A.GAS_PUBLISH_LAG_DAYS) - pd.Timedelta(hours=14),
                                                                         pd.Timedelta(days=A.GAS_PUBLISH_LAG_DAYS) + pd.Timedelta(hours=14))).all()
    wx = parse.read("weather_obs")
    assert (wx["pub_basis"] == "ASSUMPTION").all() and (wx["published_utc"] <= adopt.asof_from_name(wx["raw_file"].iloc[0])).all()
    mid = wx[wx["interval_start_utc"] < pd.Timestamp("2026-06-01", tz="UTC")]
    d = (mid["published_utc"] - mid["interval_start_utc"]) / pd.Timedelta(hours=1)
    assert d.between(41, 43).all()                                                                  # day D public ~42 h after local midnight starting D


# ------------------------------------------------------------------------------------------------ DST in the parsers
def _raw_zip(name_csv: str, csv_text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name_csv, csv_text)
    return buf.getvalue()


def test_fall_back_day_has_25_hours_and_spring_forward_has_23(dispatch_home):
    """RT file rows for 1 Nov 2026 (25 hours: hour 2 appears twice, the second with DSTFlag Y) and 14 Mar 2027 (23 hours)."""
    rows = ["DeliveryDate,DeliveryHour,DeliveryInterval,SettlementPointName,SettlementPointType,SettlementPointPrice,DSTFlag"]
    for h in range(1, 25):
        for i in range(1, 5):
            rows.append(f"11/01/2026,{h},{i},HB_NORTH,HU,{h}.{i},N")
            if h == 2:
                rows.append(f"11/01/2026,{h},{i},HB_NORTH,HU,{h}.{i}9,Y")                    # the repeated hour
    name = "cdr.00012301.0000000000000000.20261102.000202301.SPPHLZNP6905_20261102_0000_csv.zip"
    lake.put("ercot", "NP6-905-CD", parse_mis_publish(name), name, _raw_zip("x.csv", "\n".join(rows) + "\n"), "mis")
    spring = ["DeliveryDate,DeliveryHour,DeliveryInterval,SettlementPointName,SettlementPointType,SettlementPointPrice,DSTFlag"]
    for h in (1, 2) + tuple(range(4, 25)):                                                    # hour 3 does not exist on 14 Mar 2027
        for i in range(1, 5):
            spring.append(f"03/14/2027,{h},{i},HB_NORTH,HU,5.0,N")
    name2 = "cdr.00012301.0000000000000000.20270315.000202301.SPPHLZNP6905_20270315_0000_csv.zip"
    lake.put("ercot", "NP6-905-CD", parse_mis_publish(name2), name2, _raw_zip("x.csv", "\n".join(spring) + "\n"), "mis")
    parse.parse_all(["rt_spp"], strict=True)
    df = parse.read("rt_spp")
    fall = df[df["interval_start_utc"].between(pd.Timestamp("2026-11-01T05:00:00Z"), pd.Timestamp("2026-11-02T05:59:59Z"))]
    assert len(fall) == 100 and fall["interval_start_utc"].is_unique                          # 25 h x 4, every instant distinct
    t = fall.sort_values("interval_start_utc")["interval_start_utc"]
    assert (t.diff().dropna() == pd.Timedelta(minutes=15)).all()                              # no gap, no overlap
    first_pass = fall[fall["price_usd_per_mwh"] == 2.1]["interval_start_utc"].iloc[0]
    second_pass = fall[fall["price_usd_per_mwh"] == 2.19]["interval_start_utc"].iloc[0]
    assert first_pass == pd.Timestamp("2026-11-01T06:00:00Z") and second_pass == pd.Timestamp("2026-11-01T07:00:00Z")
    spr = df[df["interval_start_utc"].between(pd.Timestamp("2027-03-14T06:00:00Z"), pd.Timestamp("2027-03-15T04:59:59Z"))]
    assert len(spr) == 92 and spr["interval_start_utc"].is_unique                              # 23 h x 4
    assert (spr.sort_values("interval_start_utc")["interval_start_utc"].diff().dropna() == pd.Timedelta(minutes=15)).all()


def test_dam_hour_ending_24_and_the_repeated_hour(dispatch_home):
    rows = ["DeliveryDate,HourEnding,SettlementPoint,SettlementPointPrice,DSTFlag"]
    for he in range(1, 25):
        rows.append(f"11/01/2026,{he:02d}:00,HB_NORTH, {he}.5,N")
        if he == 2:
            rows.append("11/01/2026,02:00,HB_NORTH, 99.5,Y")                                  # repeated hour (CST pass)
    name = "cdr.00012331.0000000000000000.20261031.135445053.DAMSPNP4190_csv.zip"
    lake.put("ercot", "NP4-190-CD", parse_mis_publish(name), name, _raw_zip("x.csv", "\n".join(rows) + "\n"), "mis")
    parse.parse_all(["dam_spp"], strict=True)
    df = parse.read("dam_spp").sort_values("interval_start_utc")
    assert len(df) == 25 and df["interval_start_utc"].is_unique
    assert df["interval_start_utc"].iloc[0] == pd.Timestamp("2026-11-01T05:00:00Z")           # HE01 = 00:00-01:00 CDT
    assert df["interval_start_utc"].iloc[-1] == pd.Timestamp("2026-11-02T05:00:00Z")          # HE24 = 23:00-24:00 CST
    assert df[df["price_usd_per_mwh"] == 99.5]["interval_start_utc"].iloc[0] == pd.Timestamp("2026-11-01T07:00:00Z")
    assert (df["published_utc"] == parse_mis_publish(name)).all()                             # 13:54:45 CDT on 31 Oct


# ------------------------------------------------------------------------------------------------ the xlsx reader and the bulk datasets
def make_xlsx(sheets: dict[str, list[list]]) -> bytes:
    """A minimal valid .xlsx: values only. Strings go through sharedStrings, numbers are plain."""
    sst: list[str] = []

    def idx(s):
        if s not in sst:
            sst.append(s)
        return sst.index(s)
    sheet_xml = {}
    for si, (name, rows) in enumerate(sheets.items(), 1):
        cells = []
        for r, row in enumerate(rows, 1):
            cs = []
            for c, v in enumerate(row):
                if v is None:
                    continue
                ref = f"{chr(65 + c)}{r}"
                if v == "":                                            # an empty styled cell, as Excel writes under a merged range
                    cs.append(f'<c r="{ref}" s="20"></c>')
                elif isinstance(v, str):
                    cs.append(f'<c r="{ref}" s="1" t="s"><v>{idx(v)}</v></c>')
                else:
                    cs.append(f'<c r="{ref}" s="2"><v>{v}</v></c>')
            cells.append(f'<row r="{r}">{"".join(cs)}</row>')
        sheet_xml[si] = ('<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                         + "".join(cells) + "</sheetData></worksheet>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("xl/workbook.xml", '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                    + "".join(f'<sheet name="{n}" sheetId="{i}" r:id="rId{i}"/>' for i, n in enumerate(sheets, 1)) + "</sheets></workbook>")
        zf.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    + "".join(f'<Relationship Id="rId{i}" Type="x" Target="worksheets/Sheet{i}.xml"/>' for i in range(1, len(sheets) + 1)) + "</Relationships>")
        for i, x in sheet_xml.items():
            zf.writestr(f"xl/worksheets/Sheet{i}.xml", x)
        zf.writestr("xl/sharedStrings.xml", '<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                    + "".join(f"<si><t>{s}</t></si>" for s in sst) + "</sst>")
    return buf.getvalue()


HEADER = ["Delivery Date", "Delivery Hour", "Delivery Interval", "Repeated Hour Flag", "Settlement Point Name", "Settlement Point Type",
          "Settlement Point Price"]


def test_xlsx_reader_fast_and_general_paths_agree():
    rows = [HEADER] + [["01/01/2026", h, i, "N", "HB_NORTH", "HU", 10.5 + h + i / 10] for h in (1, 2) for i in (1, 2)]
    wb_bytes = make_xlsx({"Jan": rows, "Report Info": [["Title"], ["Delivery Date", "Delivery Date"]]})
    with xlsx.Workbook(wb_bytes) as wb:
        assert wb.sheet_names == ["Jan", "Report Info"]
        fast = wb.dataframe("Jan", ("Delivery Date",))
        header, body = wb.table("Jan", ("Delivery Date",))
        slow = pd.DataFrame(body, columns=header)
        assert fast.shape == (4, 7) and list(fast.columns) == HEADER
        pd.testing.assert_frame_equal(fast.astype(object), slow.astype(object), check_dtype=False)
        assert len(wb.dataframe("Report Info", ("Delivery Date",))) == 0              # a glossary row is not a table header
    assert [n for n, _ in xlsx.read_frames(wb_bytes, ("Delivery Date",))] == ["Jan"]


def test_xlsx_reader_survives_the_stray_tail_row_and_mixed_date_cells():
    junk = [43831, None, None, 10, None, 0.33378472]                                    # the stray summary row ERCOT leaves in some sheets
    rows = [HEADER] + [["01/01/2026", 1, i, "N", "HB_NORTH", "HU", 20 + i] for i in (1, 2, 3)] + [junk]
    with xlsx.Workbook(make_xlsx({"Oct": rows})) as wb:
        df = wb.dataframe("Oct", ("Delivery Date",))
    assert len(df) == 3 and df["Settlement Point Price"].tolist() == [21.0, 22.0, 23.0]
    mixed = [HEADER, ["01/01/2026", 1, 1, "N", "HB_NORTH", "HU", 1.0], [46023, 1, 2, "N", "HB_NORTH", "HU", 2.0]]   # text and Excel serial dates
    with xlsx.Workbook(make_xlsx({"Jan": mixed})) as wb:
        df = wb.dataframe("Jan", ("Delivery Date",))
    assert len(df) == 2
    got = parse._serial_to_naive_dates(df["Delivery Date"])
    assert got.tolist() == [pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-01")]


def test_xlsx_reader_ignores_glossary_rows_with_empty_cells_and_copes_with_holes():
    """The 2026 RT ancillary workbook has a 'Report Info' sheet whose glossary line repeats 'Delivery Date' across a merged range
    (cells A, B and an EMPTY C): the fast path once took it for a table header and died converting b'' to a float."""
    rows = [HEADER] + [["01/01/2026", 1, i, "N", "HB_NORTH", "HU", 20 + i] for i in (1, 2, 3)]
    glossary = [["Title"], ["Delivery Date", "Delivery Date", ""], ["Delivery Hour", "Hour of the day", ""], [46299, 1, 0.417]]
    wb_bytes = make_xlsx({"Report Info": glossary, "Jan": rows})
    with xlsx.Workbook(wb_bytes) as wb:
        assert len(wb.dataframe("Report Info", ("Delivery Date",))) == 0
        assert len(wb.dataframe("Jan", ("Delivery Date",))) == 3
    assert [n for n, _ in xlsx.read_frames(wb_bytes, ("Delivery Date",))] == ["Jan"]
    holes = [HEADER, ["01/01/2026", 1, 1, "N", "HB_NORTH", "HU", 1.5], ["01/01/2026", 1, 2, "N", "HB_NORTH", "HU", ""]]
    with xlsx.Workbook(make_xlsx({"Jan": holes})) as wb:
        df = wb.dataframe("Jan", ("Delivery Date",))                    # a hole inside the table goes to the general reader
    assert len(df) == 2 and df["Settlement Point Price"].iloc[0] == 1.5 and pd.isna(df["Settlement Point Price"].iloc[1])


def _hist_zip(rows, sheet="Nov") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("rpt.00013061.0000000000000000.RTMLZHBSPP_2026.xlsx", make_xlsx({sheet: rows}))
    return buf.getvalue()


def test_yearly_workbook_dataset_is_deterministic_labelled_and_handles_the_fall_back_day(dispatch_home):
    rows = [HEADER]
    for h in range(1, 25):
        for i in range(1, 5):
            for sp, typ in (("HB_NORTH", "HU"), ("LZ_NORTH", "LZ")):
                rows.append(["11/01/2026", h, i, "N", sp, typ, round(30 + h + i / 10, 2)])
                if h == 2:
                    rows.append(["11/01/2026", h, i, "Y", sp, typ, round(60 + i / 10, 2)])
    rows.append([43831, None, None, 10, None, 0.33])                                        # stray tail row
    name = "rpt.00013061.0000000000000000.20261201.080657716.RTMLZHBSPP_2026.zip"
    lake.put("ercot", "NP6-785-ER", parse_mis_publish(name), name, _hist_zip(rows), "mis")
    parse.parse_all(["rt_spp_hist"], strict=True)
    df = parse.read("rt_spp_hist")
    n = df[df["settlement_point"] == "HB_NORTH"]
    assert len(n) == 100 and n["interval_start_utc"].is_unique
    assert (n["pub_basis"] == "ASSUMPTION").all()
    lag = n["published_utc"] - n["interval_start_utc"]
    assert (lag == pd.Timedelta(minutes=15) + A.RT_PRICE_PUBLISH_LAG).all()                  # interval end + the named lag
    assert (n["published_utc"] <= parse_mis_publish(name)).all()
    two = n.sort_values("interval_start_utc")
    assert two[two["price_usd_per_mwh"] == 60.1]["interval_start_utc"].iloc[0] == pd.Timestamp("2026-11-01T07:00:00Z")
    h1 = hash_tree(dispatch_home.PARSED)
    shutil.rmtree(dispatch_home.PARSED)
    parse.parse_all(["rt_spp_hist"], strict=True)
    assert hash_tree(dispatch_home.PARSED) == h1
    # a re-published archive supersedes the old one for that year (newest posting wins), and the old rows are replaced
    rows2 = [HEADER, ["11/01/2026", 1, 1, "N", "HB_NORTH", "HU", 999.0]]
    name2 = "rpt.00013061.0000000000000000.20261208.080657716.RTMLZHBSPP_2026.zip"
    lake.put("ercot", "NP6-785-ER", parse_mis_publish(name2), name2, _hist_zip(rows2), "mis")
    parse.parse_all(["rt_spp_hist"], strict=True)
    assert parse.read("rt_spp_hist")["price_usd_per_mwh"].tolist() == [999.0]


def test_a_corrupt_raw_file_fails_the_build_loudly(dispatch_home):
    name = "cdr.00012301.0000000000000000.20261008.101702135.SPPHLZNP6905_20261008_1015_csv.zip"
    lake.put("ercot", "NP6-905-CD", parse_mis_publish(name), name, b"PK this is not a zip", "mis")
    with pytest.raises(parse.ParseError):
        parse.parse_all(["rt_spp"], strict=True)
    res = parse.parse_all(["rt_spp"], strict=False)
    assert res["rt_spp"]["errors"] and res["rt_spp"]["written"] == 0


def test_catalog_summaries_describe_the_lake(dispatch_home, seeded):
    from dispatch.data import catalog
    raw = catalog.raw_summary(pd.Timestamp("2026-10-11T00:00:00Z")).set_index("report_id")
    assert raw.loc["NP6-905-CD", "files"] == 289 and raw.loc["NP6-905-CD", "origins"] == "adopt"
    assert raw.loc["NP6-905-CD", "last_published_utc"] > raw.loc["NP6-905-CD", "first_published_utc"]
    cov = catalog.dataset_coverage().set_index("dataset")
    assert set(cov.index) == set(parse.DATASETS)
    assert cov.loc["rt_spp", "partitions"] == 3 and cov.loc["rt_spp", "rows"] > 300_000
    assert cov.loc["rt_spp", "first_interval_utc"] < cov.loc["rt_spp", "last_interval_utc"]
    assert cov.loc["rt_spp_hist", "rows"] == 0 and cov.loc["load_forecast", "partitions"] == 0
    assert cov.loc["rt_spp", "pub_basis"] == "REAL" and cov.loc["gas_hh", "pub_basis"] == "ASSUMPTION"
    fresh = catalog.freshness(pd.Timestamp("2026-10-10T12:00:00Z"))
    assert set(fresh) >= {"NP6-905-CD", "NP4-190-CD", "DHHNGSP", "GHCND"} and all(v >= 0 for v in fresh.values())


def test_bulk_groups_parse_in_worker_processes_with_identical_bytes(dispatch_home):
    """Three yearly archives = three groups = worker processes (python -m dispatch.data.parse --group ...). The caller is this
    pytest process, which has no `if __name__ == "__main__"` guard to speak of: workers must not re-import it. Same bytes as inline."""
    for year in (2024, 2025, 2026):
        rows = [HEADER] + [[f"06/01/{year}", h, i, "N", sp, "HU", round(20 + h + i / 10 + (year % 10), 2)]
                           for h in (1, 2, 3) for i in (1, 2, 3, 4) for sp in ("HB_NORTH", "HB_WEST")]
        name = f"rpt.00013061.0000000000000000.{year + 1}0101.080657716.RTMLZHBSPP_{year}.zip"
        lake.put("ercot", "NP6-785-ER", parse_mis_publish(name), name, _hist_zip(rows, sheet="Jun"), "mis")
    res = parse.parse_all(["rt_spp_hist"], workers=2, strict=True)["rt_spp_hist"]
    assert res["written"] == 3 and res["rows"] == 3 * 24 and not res["errors"]
    in_workers = hash_tree(dispatch_home.PARSED)
    assert len(in_workers) == 3
    shutil.rmtree(dispatch_home.PARSED)
    inline = parse.parse_all(["rt_spp_hist"], workers=1, strict=True)["rt_spp_hist"]
    assert inline["written"] == 3
    assert hash_tree(dispatch_home.PARSED) == in_workers
    again = parse.parse_all(["rt_spp_hist"], workers=2, strict=True)["rt_spp_hist"]
    assert again["written"] == 0 and again["skipped"] == 3


def test_a_worker_that_fails_is_reported_not_swallowed(dispatch_home):
    for year in (2024, 2025, 2026):
        name = f"rpt.00013061.0000000000000000.{year + 1}0101.080657716.RTMLZHBSPP_{year}.zip"
        lake.put("ercot", "NP6-785-ER", parse_mis_publish(name), name, b"PK this is not an archive", "mis")
    res = parse.parse_all(["rt_spp_hist"], workers=2, strict=False)["rt_spp_hist"]
    assert len(res["errors"]) == 3 and res["written"] == 0 and "ParseError" in res["errors"][0]
    with pytest.raises(parse.ParseError):
        parse.parse_all(["rt_spp_hist"], workers=2, strict=True)


# ------------------------------------------------------------------------------------------------ 60-day disclosure datasets
def _zip_members(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for n, text in members.items():
            zf.writestr(n, text)
    return buf.getvalue()


ESR_HEAD = ('"SCED Time Stamp","Repeated Hour Flag","QSE","DME","Resource Name","Resource Type","Output Schedule","HSL","HDL","LSL","LDL",'
            '"Telemetered Resource Status","Base Point","Telemetered Net Output","Ramp Rate Up","Ramp Rate Down","AS Capability REGUP",'
            '"AS Capability REGDN","AS Capability ECRS","AS Capability NSPIN","AS Capability RRSPFR","AS Capability RRSFFR","State of Charge",'
            '"Minimum SOC","Maximum SOC","AS Awards NSPIN","AS Awards RRSFFR","AS Awards RRSPFR","AS Awards RRSUFR","AS Awards ECRS",'
            '"AS Awards REGUP","AS Awards REGDN","Bid_Type"')
POISON = "this is not a csv at all\n" + '"unterminated,\n' * 3          # a member the parsers must never open


def test_esr_sced_dataset_reads_only_its_member_and_keeps_blanks_as_nan(dispatch_home):
    rows = [ESR_HEAD,
            '"08/11/2026 00:00:18","N","QA","DA","BAT1_ESR1","ESR","0","9.9","9.4","-8.5","-8.5","ON","-0.03","-0.02","1.98","1.98","1.98","1.98",'
            '".99",".33","0","0","6.018","0","25.9","0.3","","","","","","","3PT"',
            '"08/11/2026 00:00:18","N","QB","DB","BAT2_ESR1","ESR","0","0","0","-60","-60","ON","0","-0.83","24","24","24","24","5.8",".48","120",'
            '"0","6.02","6.02","59.62","","","","","5.1","","","3PT"',
            '"08/11/2026 00:05:20","N","QA","DA","BAT1_ESR1","ESR","0","9.9","9.4","-8.5","-8.5","ON","-0.03","-0.02","1.98","1.98","1.98","1.98",'
            '".99",".33","0","0","6.5","0","25.9","","","","","","","","3PT"']
    name = "ext.00013052.0000000000000000.20261010.055240368.60_Day_SCED_Disclosure.zip"
    data = _zip_members({"60d_SCED_Gen_Resource_Data-10-OCT-26.csv": POISON, "60d_ESR_Data_in_SCED-10-OCT-26.csv": "\n".join(rows) + "\n",
                         "60d_SCED_Resource_AS_OFFERS-10-OCT-26.csv": POISON})
    lake.put("ercot", "60D-SCED", parse_mis_publish(name), name, data, "adopt:test")
    res = parse.parse_dataset("esr_sced")
    assert res["written"] == 1 and res["rows"] == 3 and not res["errors"]
    df = parse.read("esr_sced")
    assert list(df["resource_name"]) == ["BAT1_ESR1", "BAT2_ESR1", "BAT1_ESR1"]                     # sorted by time, then name
    first = df.iloc[0]
    assert first["interval_start_utc"] == pd.Timestamp("2026-08-11T05:00:18Z")                       # 00:00:18 CDT
    assert (df["published_utc"] == pd.Timestamp("2026-10-10T10:52:40.368Z")).all()                    # the zip's posting (05:52:40 CDT), 60 days later
    assert (df["pub_basis"] == "REAL").all() and df["raw_file"].eq(name).all()
    assert first["soc_mwh"] == 6.018 and first["min_soc_mwh"] == 0.0 and first["max_soc_mwh"] == 25.9
    assert first["hsl_mw"] == 9.9 and first["lsl_mw"] == -8.5 and first["status"] == "ON" and first["as_award_nspin_mw"] == 0.3
    assert pd.isna(first["as_award_regup_mw"]) and df.iloc[1]["as_award_ecrs_mw"] == 5.1             # a blank award stays NaN, not 0
    assert str(df["interval_start_utc"].dtype) == "datetime64[us, UTC]" and str(df["published_utc"].dtype) == "datetime64[us, UTC]"
    assert parse.parse_dataset("esr_sced")["skipped"] == 1                                            # incremental


def test_esr_sced_repeated_hour_is_flagged_and_missing_columns_fail(dispatch_home):
    head = ESR_HEAD
    row = '"11/01/2026 01:30:05","%s","QA","DA","BAT1_ESR1","ESR","0","9.9","9.4","-8.5","-8.5","ON","0","0","1","1","1","1","1","1","0","0","5","0","20","","","","","","","","3PT"'
    name = "ext.00013052.0000000000000000.20261230.055240368.60_Day_SCED_Disclosure.zip"
    lake.put("ercot", "60D-SCED", parse_mis_publish(name), name, _zip_members({"60d_ESR_Data_in_SCED-30-DEC-26.csv": "\n".join([head, row % "N", row % "Y"]) + "\n"}), "adopt:test")
    parse.parse_dataset("esr_sced")
    df = parse.read("esr_sced")
    assert list(df["interval_start_utc"]) == [pd.Timestamp("2026-11-01T06:30:05Z"), pd.Timestamp("2026-11-01T07:30:05Z")]   # CDT, then CST
    name2 = "ext.00013052.0000000000000000.20261231.055240368.60_Day_SCED_Disclosure.zip"
    lake.put("ercot", "60D-SCED", parse_mis_publish(name2), name2, _zip_members({"60d_ESR_Data_in_SCED-31-DEC-26.csv": "A,B\n1,2\n"}), "adopt:test")
    name3 = "ext.00013052.0000000000000000.20270101.055240368.60_Day_SCED_Disclosure.zip"
    lake.put("ercot", "60D-SCED", parse_mis_publish(name3), name3, _zip_members({"60d_SCED_Gen_Resource_Data-01-JAN-27.csv": POISON}), "adopt:test")
    res = parse.parse_dataset("esr_sced")
    assert res["written"] == 0 and len(res["errors"]) == 2 and any("lacks columns" in e for e in res["errors"]) and any("no member" in e for e in res["errors"])


def test_dam_spp_60d_joins_bid_and_offer_awards_and_refuses_conflicts(dispatch_home):
    bids = ('"Delivery Date","Hour Ending","Settlement Point","QSE Name","Energy Only Bid Award in MW","Settlement Point Price","Bid ID"\n'
            '"08/11/2026","1","LZ_NORTH","QA","-136","23.85","b1"\n"08/11/2026","1","HB_NORTH","QA","0","23.91","b2"\n'
            '"08/11/2026","24","HB_NORTH","QB","-5","30.25","b3"\n')
    offers = ('"Delivery Date","Hour Ending","Settlement Point","QSE Name","Energy Only Offer Award in MW","Settlement Point Price","Offer ID"\n'
              '"08/11/2026","1","HB_NORTH","QC","1","23.91","o1"\n"08/11/2026","1","BAT1_RN","QC",".2","24.23","o2"\n')
    name = "ext.00013051.0000000000000000.20261010.063747402.60_Day_DAM_Disclosure.zip"
    lake.put("ercot", "60D-DAM", parse_mis_publish(name), name, _zip_members({
        "60d_DAM_EnergyBidAwards-10-OCT-26.csv": bids, "60d_DAM_EnergyOnlyOfferAwards-10-OCT-26.csv": offers,
        "60d_DAM_PTPObligationBids-10-OCT-26.csv": POISON}), "adopt:test")
    res = parse.parse_dataset("dam_spp_60d")
    assert res["written"] == 1 and res["rows"] == 4 and not res["errors"]
    df = parse.read("dam_spp_60d").set_index(["settlement_point", "interval_start_utc"])
    assert df.loc[("HB_NORTH", pd.Timestamp("2026-08-11T05:00:00Z")), "price_usd_per_mwh"] == 23.91          # in both files: one row
    assert df.loc[("HB_NORTH", pd.Timestamp("2026-08-12T04:00:00Z")), "price_usd_per_mwh"] == 30.25          # hour ending 24 starts at 23:00 CDT
    assert df.loc[("BAT1_RN", pd.Timestamp("2026-08-11T05:00:00Z")), "price_usd_per_mwh"] == 24.23
    assert (df["published_utc"] == pd.Timestamp("2026-10-10T11:37:47.402Z")).all() and (df["pub_basis"] == "REAL").all()
    bad = offers.replace('"HB_NORTH","QC","1","23.91"', '"HB_NORTH","QC","1","24.00"')
    name2 = "ext.00013051.0000000000000000.20261011.063747402.60_Day_DAM_Disclosure.zip"
    lake.put("ercot", "60D-DAM", parse_mis_publish(name2), name2, _zip_members({
        "60d_DAM_EnergyBidAwards-11-OCT-26.csv": bids, "60d_DAM_EnergyOnlyOfferAwards-11-OCT-26.csv": bad}), "adopt:test")
    res = parse.parse_dataset("dam_spp_60d")
    assert res["written"] == 0 and res["skipped"] == 1 and len(res["errors"]) == 1 and "two different day-ahead prices" in res["errors"][0]
