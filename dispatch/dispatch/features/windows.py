"""Window, gather and order-statistic helpers behind the rt_v1 columns (1.1.0 adds the ones specified in notes/d1a.md section 10).

Pure numpy: no clock, no I/O, and no comparison of a time with a decision time (the point-in-time rule lives in `build.py`, in
`_known_prefix` and `_visible`, and nowhere else). Inputs are arrays whose LAST axis is the window, oldest value first; NaN means
"missing" and is skipped, never filled; a window needs at least `minp` valid values (the spec says w/2) or the result is NaN.

Exact and independent of the loaded range. Sums are elementwise IEEE additions in a fixed order (oldest value first), order
statistics come from a sort (the sorted multiset does not depend on the algorithm), so the same window gives the same bits in
`training_frame` over any range, in `point_in_time`, and in any rebuild. That is what Gate 1 (rebuild equality) and the train/serve
parity test rely on; pandas' rolling keeps a running sum and does not have the property.

Conventions match numpy where D1a's interim builder uses numpy: quantiles use the linear method (`np.quantile`, `_lerp` form), the
median of an even count is the mean of the two middle values (`np.median`), the standard deviation is the POPULATION one (ddof 0).
"""
from __future__ import annotations

import numpy as np


# ------------------------------------------------------------------------------------------------ gathers
def window_ending(arr: np.ndarray, end: np.ndarray, w: int, ok=None) -> np.ndarray:
    """(D, w) windows of `arr` ending at index `end[d]` (inclusive), oldest first. NaN where the index is outside `arr` or `ok[d]` is False."""
    end = np.asarray(end, dtype=np.int64)
    idx = end[:, None] - np.arange(w - 1, -1, -1, dtype=np.int64)[None, :]
    inside = (idx >= 0) & (idx < len(arr))
    if ok is not None:
        inside &= np.asarray(ok)[:, None]
    return np.where(inside, arr[np.clip(idx, 0, max(len(arr) - 1, 0))], np.nan)


def tail(win: np.ndarray, w: int) -> np.ndarray:
    """The newest `w` values of windows built with `window_ending` (a suffix, so a 96-window is the tail of a 672-window)."""
    return win[..., -w:]


# ------------------------------------------------------------------------------------------------ sums and counts
def ordered_sum(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(sum, count) of the valid values along the last axis, added oldest first."""
    ok = ~np.isnan(x)
    z = np.where(ok, x, 0.0)
    s = np.zeros(x.shape[:-1])
    n = np.zeros(x.shape[:-1], dtype=np.int64)
    for c in range(x.shape[-1]):
        s += z[..., c]
        n += ok[..., c]
    return s, n


def mean_of(x: np.ndarray, minp: int = 1) -> np.ndarray:
    s, n = ordered_sum(x)
    return np.where(n >= max(minp, 1), s / np.maximum(n, 1), np.nan)


def std_of(x: np.ndarray, minp: int = 1) -> np.ndarray:
    """Population standard deviation (ddof 0), two-pass: accurate to 1e-12 against exact arithmetic even after a price spike."""
    s, n = ordered_sum(x)
    m = s / np.maximum(n, 1)
    ss, _ = ordered_sum(np.square(x - m[..., None]))
    return np.where(n >= max(minp, 1), np.sqrt(ss / np.maximum(n, 1)), np.nan)


def mean_abs(x: np.ndarray, minp: int = 1) -> np.ndarray:
    return mean_of(np.abs(x), minp)


def share(x: np.ndarray, test, minp: int = 1) -> np.ndarray:
    """Share of the valid values for which `test(values)` holds (NaN if fewer than `minp` are valid)."""
    ok = ~np.isnan(x)
    n = ok.sum(axis=-1)
    with np.errstate(invalid="ignore"):
        hit = (test(np.where(ok, x, 0.0)) & ok).sum(axis=-1)
    return np.where(n >= max(minp, 1), hit / np.maximum(n, 1), np.nan)


# ------------------------------------------------------------------------------------------------ order statistics
def sort_valid(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(sorted values with the NaNs last, number of valid values) along the last axis."""
    return np.sort(x, axis=-1), (~np.isnan(x)).sum(axis=-1)


def take_sorted(s: np.ndarray, idx: np.ndarray) -> np.ndarray:
    return np.take_along_axis(s, np.asarray(idx, dtype=np.int64)[..., None], axis=-1)[..., 0]


def quantile(s: np.ndarray, n: np.ndarray, q: float, minp: int = 1) -> np.ndarray:
    """`np.quantile(valid values, q)` (method 'linear') from the output of `sort_valid`; NaN if fewer than `minp` are valid."""
    ok = n >= max(minp, 1)
    pos = np.where(ok, (n - 1) * q, 0.0)
    lo = np.floor(pos).astype(np.int64)
    hi = np.minimum(lo + 1, np.maximum(n - 1, 0))
    t = pos - lo
    a, b = take_sorted(s, lo), take_sorted(s, hi)
    diff = b - a
    r = a + diff * t
    r = np.where(t >= 0.5, b - diff * (1 - t), r)                      # numpy's symmetric lerp
    return np.where(ok, r, np.nan)


def median(s: np.ndarray, n: np.ndarray, minp: int = 1) -> np.ndarray:
    """`np.median` of the valid values: the middle one, or the mean of the two middle ones."""
    ok = n >= max(minp, 1)
    a = take_sorted(s, np.where(ok, (n - 1) // 2, 0))
    b = take_sorted(s, np.where(ok, n // 2, 0))
    return np.where(ok, (a + b) / 2.0, np.nan)


def minimum(s: np.ndarray, n: np.ndarray, minp: int = 1) -> np.ndarray:
    ok = n >= max(minp, 1)
    return np.where(ok, take_sorted(s, np.zeros(n.shape, dtype=np.int64)), np.nan)


def maximum(s: np.ndarray, n: np.ndarray, minp: int = 1) -> np.ndarray:
    ok = n >= max(minp, 1)
    return np.where(ok, take_sorted(s, np.where(ok, n - 1, 0)), np.nan)


def spread(s: np.ndarray, n: np.ndarray, minp: int = 1, k: int = 8) -> np.ndarray:
    """Mean of the k largest minus mean of the k smallest valid values (the arena's daily spread, k = 8 on 96 intervals)."""
    ok = n >= max(minp, 2 * k)
    top = np.zeros(n.shape)
    bottom = np.zeros(n.shape)
    for i in range(k):
        top += take_sorted(s, np.where(ok, n - k + i, 0))
        bottom += take_sorted(s, np.where(ok, i, 0))
    return np.where(ok, top / k - bottom / k, np.nan)


# ------------------------------------------------------------------------------------------------ the anchor
def anchor_grid(price: np.ndarray, days: int = 7, step: int = 96) -> np.ndarray:
    """A[i] = median of {price[i - step*m] : m = 1..days, i - step*m >= 0, not NaN}: the median of the same time of day over
    the previous `days` days, for every slot i of the grid (NaN when none of them exists). It reads only prices at least one
    day older than slot i, so it is known whenever price[i] is."""
    n = len(price)
    stack = np.full((n, days), np.nan)
    for m in range(1, days + 1):
        sh = step * m
        if sh < n:
            stack[sh:, m - 1] = price[: n - sh]
    s, cnt = sort_valid(stack)
    return median(s, cnt)
