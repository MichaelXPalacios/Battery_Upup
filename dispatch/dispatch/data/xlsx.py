"""A minimal, dependency-free .xlsx reader.

ERCOT publishes its yearly price archives as Excel workbooks and the platform venv has no openpyxl, so this reads the
OOXML directly: workbook.xml for sheet names, sharedStrings.xml for text, and each sheet's <row>/<c> elements for cells.

Scope: values only (strings, numbers, booleans). No formulas or styles; a numeric date cell comes back as a float
(`excel_serial_to_date` converts it). That is all the ERCOT archives need.

Two readers: `Workbook.rows()` is general and slow-ish; `Workbook.dataframe()` takes a vectorised fast path for the regular
tables ERCOT publishes (a full cell in every column of every row, one type per column) and falls back to `rows()` otherwise.
"""
from __future__ import annotations

import html
import io
import re
import zipfile
from datetime import date, timedelta
from typing import Iterator, Optional, Union

import numpy as np
import pandas as pd

_CELLS = re.compile(rb'<c r="([A-Z]+)(\d+)"([^>]*?)(?:/>|>(.*?)</c>)', re.S)      # general: any cell content
_CELLS_FAST = re.compile(rb'<c r="([A-Z]+)(\d+)"([^>]*)>(?:<v>([^<]*)</v>)?</c>')   # value cells only: ~2x faster
_T = re.compile(rb"<t\b[^>]*>(.*?)</t>", re.S)
_SI = re.compile(rb"<si\b[^>]*>(.*?)</si>", re.S)
_SHEET = re.compile(rb'<sheet\b([^>]*?)/>')


def _attr(name: bytes, blob: bytes) -> Optional[bytes]:
    m = re.search(rb'\b' + name + rb'="([^"]*)"', blob)
    return m.group(1) if m else None


def col_index(letters: bytes) -> int:
    """b'A' -> 0, b'Z' -> 25, b'AA' -> 26."""
    n = 0
    for ch in letters:
        n = n * 26 + (ch - 64)
    return n - 1


def excel_serial_to_date(serial: float) -> date:
    """Excel 1900 date serial -> date (the 1900 leap-year bug makes the epoch 1899-12-30 for serials after Feb 1900)."""
    return date(1899, 12, 30) + timedelta(days=int(serial))


class Workbook:
    def __init__(self, source: Union[bytes, str, "os.PathLike", io.IOBase]):
        if isinstance(source, (bytes, bytearray)):
            source = io.BytesIO(source)
        self._zf = zipfile.ZipFile(source)
        self._names = {n.lower(): n for n in self._zf.namelist()}
        self._sst: Optional[list[str]] = None
        self.sheet_names, self._targets = self._sheets()

    def close(self):
        self._zf.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def _read(self, name: str) -> bytes:
        return self._zf.read(self._names[name.lower().lstrip("/")])

    def _sheets(self):
        wb = self._read("xl/workbook.xml")
        rels = self._read("xl/_rels/workbook.xml.rels")
        rid_target = {}
        for m in re.finditer(rb"<Relationship\b([^>]*?)/?>", rels):
            a = m.group(1)
            i, t = _attr(b"Id", a), _attr(b"Target", a)
            if i and t:
                rid_target[i.decode()] = t.decode()
        names, targets = [], []
        for m in _SHEET.finditer(wb):
            a = m.group(1)
            nm = _attr(b"name", a)
            rid = re.search(rb'\br:id="([^"]*)"', a) or re.search(rb'\bid="([^"]*)"', a)
            if not nm or not rid:
                continue
            tgt = rid_target[rid.group(1).decode()].lstrip("/")
            names.append(html.unescape(nm.decode("utf-8")))
            targets.append(tgt if tgt.lower().startswith("xl/") else "xl/" + tgt)
        return names, targets

    @property
    def shared_strings(self) -> list[str]:
        if self._sst is None:
            self._sst = []
            if "xl/sharedstrings.xml" in self._names:
                data = self._read("xl/sharedStrings.xml")
                for si in _SI.finditer(data):
                    self._sst.append("".join(html.unescape(t.decode("utf-8")) for t in _T.findall(si.group(1))))
        return self._sst

    def _sheet_index(self, sheet: Union[str, int]) -> int:
        return self.sheet_names.index(sheet) if isinstance(sheet, str) else int(sheet)

    # ------------------------------------------------------------------------------------------ general reader
    def rows(self, sheet: Union[str, int]) -> Iterator[tuple[int, list]]:
        """Yield (row_number, values) for every non-empty row; values run from column A to the last non-empty cell,
        with None for empty cells."""
        data = self._read(self._targets[self._sheet_index(sheet)])
        sst = self.shared_strings
        cur_b, cur, vals = None, 0, []
        for col, rown, attrs, inner in _CELLS.findall(data):
            if not inner:
                continue
            if inner.startswith(b"<v>"):
                raw = inner[3: inner.index(b"</v>")]
                if b't="s"' in attrs:
                    val = sst[int(raw)]
                elif b" t=" not in attrs or b't="n"' in attrs:
                    val = float(raw) if raw else None
                elif b't="b"' in attrs:
                    val = raw == b"1"
                else:                                        # t="str" / "e" / "d": text
                    val = html.unescape(raw.decode("utf-8"))
            elif b"<is>" in inner:
                val = "".join(html.unescape(x.decode("utf-8")) for x in _T.findall(inner)) or None
            else:
                continue
            if val is None:
                continue
            if rown != cur_b:
                if vals:
                    yield cur, vals
                cur_b, cur, vals = rown, int(rown), []
            ci = (col[0] - 65) if len(col) == 1 else col_index(col)
            n = len(vals)
            if n <= ci:
                if n < ci:
                    vals.extend([None] * (ci - n))
                vals.append(val)
            else:
                vals[ci] = val
        if vals:
            yield cur, vals

    def table(self, sheet: Union[str, int], header_first_cells: tuple = ()) -> tuple[list[str], list[list]]:
        """(header, data rows) of a sheet. The header is the first row whose first cell is in `header_first_cells`
        (or simply the first row if that is empty). Rows are padded to the header width."""
        header: Optional[list[str]] = None
        out: list[list] = []
        for _, vals in self.rows(sheet):
            if header is None:
                if (not header_first_cells or (isinstance(vals[0], str) and vals[0].strip() in header_first_cells))                         and sum(c is not None for c in vals) >= 3:
                    header = [str(c).strip() if c is not None else "" for c in vals]
                continue
            if len(vals) < len(header):
                vals = vals + [None] * (len(header) - len(vals))
            out.append(vals[: len(header)])
        return header or [], out

    # ------------------------------------------------------------------------------------------ fast reader
    def dataframe(self, sheet: Union[str, int], header_first_cells: tuple = ()) -> pd.DataFrame:
        """A sheet as a DataFrame (header row found as in `table`). Strings stay strings, numbers become float64.
        Empty DataFrame when the sheet has no header row."""
        fast = self._dataframe_fast(sheet, header_first_cells)
        if fast is not None:
            return fast
        header, rows = self.table(sheet, header_first_cells)
        return pd.DataFrame(rows, columns=header) if header else pd.DataFrame()

    def _dataframe_fast(self, sheet, header_first_cells) -> Optional[pd.DataFrame]:
        data = self._read(self._targets[self._sheet_index(sheet)])
        if b"<is>" in data or b't="inlineStr"' in data:
            return None
        sst = self.shared_strings
        cells = _CELLS_FAST.findall(data)
        if not cells:
            return None
        cols, rows, attrs, vals = (list(x) for x in zip(*cells))
        # header: the first column-A cell whose shared string is in header_first_cells (or the first one if none are given)
        start = None
        for k, (c, a, v) in enumerate(zip(cols, attrs, vals)):
            if c == b"A" and b't="s"' in a and v:
                text = sst[int(v)].strip()
                if not header_first_cells or text in header_first_cells:
                    k2 = k
                    while k2 < len(rows) and rows[k2] == rows[k]:
                        k2 += 1
                    if k2 - k >= 3 and all(vals[k:k2]):  # a real table header, not a glossary line that repeats the name (and has empty cells)
                        start = k
                        break
        if start is None:
            return None
        hrow = rows[start]
        k_end = start
        while k_end < len(rows) and rows[k_end] == hrow:
            k_end += 1
        width = k_end - start
        body = k_end
        if width == 0:
            return None
        letters = cols[start:k_end]
        # the body must be whole rows with a cell in every header column. A few irregular cells at the very end (ERCOT leaves a
        # stray summary row in some October sheets) are dropped; anything more irregular goes to the general reader.
        cc = np.asarray(cols[body:], dtype="S3")
        expected = np.tile(np.asarray(letters, dtype="S3"), len(cc) // width + 1)[: len(cc)]
        bad = np.flatnonzero(cc != expected)
        n_full = len(cc) // width if bad.size == 0 else int(bad[0]) // width
        if bad.size == 0 and len(cc) % width:
            n_full = len(cc) // width
        if len(cc) - n_full * width > 5 * width:
            return None
        end = body + n_full * width
        if not all(vals[body:end]):                  # an empty cell inside the table: the general reader copes with that
            return None
        cols, rows, attrs, vals = cols[:end], rows[:end], attrs[:end], vals[:end]
        header = [sst[int(vals[start + j])].strip() if b't="s"' in attrs[start + j] else "" for j in range(width)]
        sst_arr = np.asarray(sst, dtype=object)
        out = {}
        for j in range(width):
            a_j = attrs[body + j::width]
            v_j = vals[body + j::width]
            if not v_j:
                out[header[j]] = np.array([], dtype=object)
                continue
            kind_of = {a: ("s" if b't="s"' in a else ("n" if (b" t=" not in a or b't="n"' in a) else "x")) for a in set(a_j)}
            kinds = set(kind_of.values())
            raw = np.asarray(v_j, dtype="S")
            if kinds == {"s"}:
                out[header[j]] = sst_arr[raw.astype(np.int64)]
            elif kinds == {"n"}:
                out[header[j]] = raw.astype(np.float64)
            elif kinds == {"s", "n"}:                             # e.g. a date column with some text and some serial numbers
                is_s = np.fromiter((kind_of[a] == "s" for a in a_j), dtype=bool, count=len(a_j))
                col = np.empty(len(a_j), dtype=object)
                col[is_s] = sst_arr[raw[is_s].astype(np.int64)]
                col[~is_s] = raw[~is_s].astype(np.float64)
                out[header[j]] = col
            else:
                return None
        return pd.DataFrame(out)


def read_frames(source, header_first_cells: tuple = ()) -> Iterator[tuple[str, pd.DataFrame]]:
    """Every sheet with a header row as (sheet name, DataFrame). Sheets without one (e.g. 'Report Info') are skipped."""
    with Workbook(source) as wb:
        for name in wb.sheet_names:
            df = wb.dataframe(name, header_first_cells)
            if len(df):
                yield name, df
