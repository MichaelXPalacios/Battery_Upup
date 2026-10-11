"""Henry Hub daily spot price from EIA's keyless history page (dataset `gas_hh`).

    page:  https://www.eia.gov/dnav/ng/hist/rngwhhdD.htm        (the XLS twin, RNGWHHDd.xls, is BIFF: nothing here reads it)
    lake:  eia / RNGWHHD / <UTC date of the release> / rngwhhdD.rel<YYYYMMDD>.<sha8>.htm

The page is a table of weeks (Mon..Fri columns, one row a week, a blank cell where there was no price), with the footer lines
`Release Date: 10/7/2026` and `Next Release Date: 10/15/2026` and, in an HTML comment, the time the page was generated
(`10/7/2026 9:14:54 AM`, US Eastern; the HTTP `Last-Modified` header says the same instant in GMT). EIA re-releases it weekly,
on Wednesday morning, with the prices through the Tuesday before.

Publish time. The raw file's `published_utc` is the release stamp (footer, else Last-Modified), capped at the moment we fetched
it: the page cannot have been public before that stamp, and it is the source's own statement, not our assumption. A re-fetch of
an unchanged page is the same bytes, the same name, and so a no-op in the lake: the stamp of the first fetch stays. Rows are
published when the page that first carried their value was released (`parse.parse_gas_hh`); a later page that carries a
different value for a trade date adds a new version of it, and the old one stays.

Downloaded content is data. The page is read with regular expressions; nothing in it is executed or followed.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from typing import Callable, Optional

import pandas as pd

from . import assumptions as A
from . import lake
from .ercot import FetchError, Fetcher
from .times import CPT, iso_z, utc

EIA_URL = "https://www.eia.gov/dnav/ng/hist/rngwhhdD.htm"
SOURCE, REPORT_ID = "eia", "RNGWHHD"
MIN_ROWS = 1000                                   # the history since 1997 has ~7,000 prices; a page with fewer is not this page

_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_ROW = re.compile(r"<tr>\s*<td class=['\"]B6['\"]>(?:&nbsp;|\s)*(\d{4}) ([A-Za-z]{3})-\s*(\d{1,2}) to ([A-Za-z]{3})-\s*(\d{1,2})</td>(.*?)</tr>",
                  re.S)
_CELL = re.compile(r"<td class=['\"]B3['\"]>(.*?)</td>", re.S)
_STAMP = re.compile(r"<!--\s*(\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}:\d{2} [AP]M)\s*-->")
_RELEASE = re.compile(r">\s*Release Date:\s*(\d{1,2}/\d{1,2}/\d{4})\s*<")
_NEXT_RELEASE = re.compile(r">\s*Next Release Date:\s*(\d{1,2}/\d{1,2}/\d{4})\s*<")
_TITLE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")
_BLANK = {"", "NA", "W", "-", "--", "&NBSP;", "NA&NBSP;"}


class GasPageError(ValueError):
    """The bytes are not EIA's Henry Hub history page (or it changed shape)."""


def new_fetcher() -> Fetcher:
    """One request at a time, A.FETCH_MIN_SPACING_S apart, a few retries with backoff; identifies itself, sends nothing personal."""
    import httpx
    client = httpx.Client(headers={"User-Agent": A.FETCH_USER_AGENT, "Accept": "text/html"}, timeout=90.0, follow_redirects=True)
    return Fetcher(client, max_concurrent=1, min_spacing_s=A.FETCH_MIN_SPACING_S, retries=A.FETCH_RETRIES)


# ------------------------------------------------------------------------------------------------ the page
def parse_history_html(body: bytes) -> pd.DataFrame:
    """The page -> DataFrame[obs_date ('YYYY-MM-DD', the trade date), price (float, $/MMBtu)], one row per day that has a price, oldest first.

    Strict where a silent mistake would shift prices by a day: every week label must start on a Monday and every row must have five cells; a
    cell is a number or one of EIA's blank marks, anything else raises. Raises GasPageError."""
    text = body.decode("utf-8", errors="replace")
    t = _TITLE.search(text)
    if not t or "Henry Hub" not in t.group(1):
        raise GasPageError("not the Henry Hub history page (title %r)" % (t.group(1).strip()[:60] if t else None))
    days, prices = [], []
    for m in _ROW.finditer(text):
        year, mon, day = int(m.group(1)), m.group(2).lower(), int(m.group(3))
        if mon not in _MONTHS:
            raise GasPageError(f"unreadable week label {m.group(0)[:80]!r}")
        monday = date(year, _MONTHS[mon], day)
        if monday.weekday() != 0:
            raise GasPageError(f"week label {year} {mon}-{day} does not start on a Monday")
        cells = [re.sub(r"<[^>]*>", "", c).strip() for c in _CELL.findall(m.group(6))]
        if len(cells) != 5:
            raise GasPageError(f"week of {monday} has {len(cells)} cells, expected 5")
        for i, c in enumerate(cells):
            if c.upper() in _BLANK:
                continue
            if not _NUMBER.match(c):
                raise GasPageError(f"week of {monday}: cell {c!r} is neither a price nor a blank mark")
            days.append((monday + timedelta(days=i)).isoformat())
            prices.append(float(c))
    if not days:
        raise GasPageError("no price rows found")
    out = pd.DataFrame({"obs_date": days, "price": prices})
    if not out["obs_date"].is_monotonic_increasing or out["obs_date"].duplicated().any():
        raise GasPageError("trade dates are not strictly increasing")
    if not out["price"].between(-50.0, 1000.0).all():
        raise GasPageError("a price outside [-50, 1000] $/MMBtu")
    return out


def page_stamp(body: bytes) -> Optional[pd.Timestamp]:
    """The moment the page was generated, from the footer comment (US Eastern wall clock) -> UTC; None if the page has none."""
    m = _STAMP.search(body.decode("utf-8", errors="replace"))
    if not m:
        return None
    naive = pd.to_datetime(m.group(1), format="%m/%d/%Y %I:%M:%S %p")
    return naive.tz_localize(A.EIA_PAGE_TZ, ambiguous=False, nonexistent="shift_forward").tz_convert("UTC").as_unit("us")


def release_dates(body: bytes) -> tuple:
    """(Release Date, Next Release Date) as 'YYYY-MM-DD' or None, from the footer lines."""
    text = body.decode("utf-8", errors="replace")

    def one(rx):
        m = rx.search(text)
        return pd.to_datetime(m.group(1), format="%m/%d/%Y").strftime("%Y-%m-%d") if m else None
    return one(_RELEASE), one(_NEXT_RELEASE)


def release_stamp(body: bytes, last_modified: Optional[str] = None) -> Optional[pd.Timestamp]:
    """When this page was released: the footer stamp, else the HTTP Last-Modified header, else None."""
    t = page_stamp(body)
    if t is not None:
        return t
    if last_modified:
        try:
            return utc(pd.Timestamp(parsedate_to_datetime(last_modified)))
        except (TypeError, ValueError):
            return None
    return None


# ------------------------------------------------------------------------------------------------ fetch
def fetch_history(now=None, *, fetcher: Optional[Fetcher] = None, min_rows: int = MIN_ROWS, log: Callable[[str], None] = lambda s: None) -> dict:
    """Fetch the history page once and store it raw (origin 'web').

    published_utc = min(release stamp, now): the source's own statement of when the page went out, never later than we held it (no stamp: now).
    The name is `rngwhhdD.rel<YYYYMMDD of that stamp>.<first 8 hex of the sha256>.htm`, so the same page fetched again is a no-op in the
    lake and a page with any difference is a new file next to the old one. Nothing is stored unless the bytes parse as the Henry Hub page with
    at least `min_rows` (MIN_ROWS) prices and none dated in the future. Returns a summary; raises FetchError."""
    f = fetcher or new_fetcher()
    t_fetch = utc(now) if now is not None else pd.Timestamp.now(tz="UTC")
    r = f.request("GET", EIA_URL)
    if r.status_code != 200:
        raise FetchError(f"EIA HTTP {r.status_code}")
    body = r.content
    if len(body) > A.FETCH_MAX_BYTES:
        raise FetchError(f"EIA response of {len(body)} bytes is above the {A.FETCH_MAX_BYTES} byte limit")
    try:
        page = parse_history_html(body)
    except GasPageError as e:
        raise FetchError(f"EIA page rejected: {e}") from e
    if len(page) < min_rows:
        raise FetchError(f"EIA page has {len(page)} prices, expected at least {min_rows}")
    newest = page["obs_date"].iloc[-1]
    if newest > t_fetch.tz_convert(CPT).strftime("%Y-%m-%d"):
        raise FetchError(f"EIA page has a price dated {newest}, in the future")
    stamp = release_stamp(body, r.headers.get("Last-Modified"))
    published = min(stamp, t_fetch) if stamp is not None else t_fetch
    rel, nxt = release_dates(body)
    name = f"rngwhhdD.rel{published.strftime('%Y%m%d')}.{hashlib.sha256(body).hexdigest()[:8]}.htm"
    new = not lake.has(SOURCE, REPORT_ID, name)
    lake.put(SOURCE, REPORT_ID, published, name, body, "web")
    newest_end = (pd.Timestamp(newest) + pd.Timedelta(days=1)).tz_localize(CPT, ambiguous=False, nonexistent="shift_forward")
    out = {"source": SOURCE, "report_id": REPORT_ID, "fetched_utc": iso_z(t_fetch), "published_utc": iso_z(published),
           "stamp_basis": "page footer" if page_stamp(body) is not None else ("Last-Modified" if stamp is not None else "fetch time"),
           "release_date": rel, "next_release_date": nxt, "file": name, "bytes": len(body), "new": new, "changed": new, "rows": len(page),
           "newest_obs_date": newest, "lag_days": round((published - newest_end).total_seconds() / 86400, 3)}
    log(f"  EIA Henry Hub: {len(page)} prices, newest {newest}, released {iso_z(published)} ({out['stamp_basis']}), "
        f"{'stored' if new else 'identical to the page already in the lake'}")
    return out
