"""Run the arena's dispatch over many price lanes in parallel, and merge the results.

`run_lanes` splits the lanes into chunks, runs `nodal_market.run_slice` on each chunk in its own process (spawn, as on
Windows) and merges the per-lane ledgers and the learner's picks back in the original lane order. `oracle_lanes` does the
same for the perfect-foresight ceiling (`arena.battery.oracle_days`).

Why chunking is exact. `run_combined` acts elementwise on lanes. The only places where the arena couples lanes are the
pooled analog standardisation and the pooled kernel bandwidth in its backtest context; the default `context="lane"`
replaces both with lane-safe versions (see nodal_market.py), so a lane's numbers do not depend on its chunk-mates.
tests/test_3a_runner.py checks single-process == chunked, lane by lane, exactly.

Entry points that start worker processes must sit behind `if __name__ == "__main__":` on Windows (spawn). The worker
functions here are module level so they can be pickled; `run_grid.py` already guards its main().

Typical use:

    from gridsite import runner
    res = runner.run_lanes(lane_ids, P, ref=ref, dates=dates, lane_zone=lane_zone, spec="grid4h",
                           start="2023-01-01", end="2026-09-26", workers=8, chunk=16)
    res.usd_kw_yr("A5", 2025)             # (lanes,) $/kW-yr, partial years annualised like the arena
    ceiling = runner.oracle_lanes(P[day_index], spec="grid4h", workers=8)    # (days, lanes) $/day per MW

For hundreds of lanes pass `provider` instead of `P`: a picklable callable `provider(lane_ids) -> (days, len(lane_ids),
96)` that each worker calls for its own chunk, so the parent never holds all prices in memory.
"""
from __future__ import annotations

import hashlib
import json
import math
import multiprocessing as mp
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from . import paths

paths.use_arena()
from . import nodal_market as N  # noqa: E402
from . import specs  # noqa: E402

try:                                         # BLAS/LAPACK thread control: same numerics in every process
    from threadpoolctl import threadpool_limits
except Exception:                            # pragma: no cover
    threadpool_limits = None

DEFAULT_WORKERS = min(8, os.cpu_count() or 1)   # ASSUMPTION: the brief says 8 cores; the box has 8 logical (4 physical)
DEFAULT_CHUNK_LANES = 16                         # DERIVED: of 8, 16 and 32 lanes a chunk, 16 gave the best lane-days/s at 8 workers (notes/phase3a.md section 5)
MAX_CHUNK_LANES = 64                             # ASSUMPTION: keeps one worker under ~0.5 GB with the full 2011-2026 history
DEFAULT_ORACLE_CHUNK_LANES = 2                   # ASSUMPTION: oracle DP arrays are days x S x cap; keep them small
ORACLE_DAY_BLOCK = 400                           # ASSUMPTION: days per oracle DP block (memory); blocks are exact
EXPERT_NAMES = ["Clockwork", "Forecaster", "Weather-regime", "Spike hunter q97", "Spike hunter q90",
                "Forecaster (low carry)"]        # REAL: arena.algorithms.Adaptive.expert_names (index = learner pick)
_THREAD_ENV = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")


# --------------------------------------------------------------------------- small utilities
def peak_rss_mb():
    """Peak working set of this process in MB (Windows via psapi, elsewhere via resource); nan if unavailable."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            pmc = PMC()
            pmc.cb = ctypes.sizeof(PMC)
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            ps = ctypes.WinDLL("psapi", use_last_error=True)
            k32.GetCurrentProcess.restype = wintypes.HANDLE
            ps.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
            ps.GetProcessMemoryInfo.restype = wintypes.BOOL
            ok = ps.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
            return pmc.PeakWorkingSetSize / 1e6 if ok else float("nan")
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e3
    except Exception:                        # pragma: no cover
        return float("nan")


class _OneThread:
    """Context manager: one BLAS thread in this process (and restore afterwards)."""

    def __enter__(self):
        self.ctx = threadpool_limits(limits=1) if threadpool_limits else None
        return self

    def __exit__(self, *a):
        if self.ctx is not None:
            self.ctx.restore_original_limits()


def plan_chunks(n_lanes, workers=None, chunk=None):
    """[(start, stop), ...] lane slices. Default chunk size: DEFAULT_CHUNK_LANES, but no more chunks than needed to keep
    every worker busy at least once (small jobs are split finer so all workers get a share)."""
    workers = DEFAULT_WORKERS if workers is None else workers
    if chunk is None:
        chunk = DEFAULT_CHUNK_LANES
        if n_lanes < workers * chunk:                       # too few lanes to give each worker a full chunk
            chunk = max(1, math.ceil(n_lanes / workers))
    chunk = int(max(1, min(chunk, MAX_CHUNK_LANES)))
    return [(a, min(a + chunk, n_lanes)) for a in range(0, n_lanes, chunk)]


# --------------------------------------------------------------------------- results container
class LaneResults:
    """Merged ledgers for many lanes: algos[A1..A5][profit|energy|wear|mwh] are (days, lanes) arrays; picks is the
    learner's expert index per day and lane (names in EXPERT_NAMES)."""

    def __init__(self, lanes, dates, spec, context, algos, picks, backup=None, timing=None):
        self.lanes, self.dates, self.spec, self.context = list(lanes), np.asarray(dates).astype(str), spec, context
        self.algos, self.picks, self.backup, self.timing = algos, picks, backup, timing or {}
        self.years = np.array([int(d[:4]) for d in self.dates])

    # ---- views
    def lane_index(self, lane):
        return self.lanes.index(lane)

    def _sel(self, year):
        return np.ones(len(self.dates), bool) if year is None else (self.years == year)

    def usd_kw_yr(self, algo, year=None, annualise=True):
        """$/kW-yr per lane for one calendar year (None = all days, divided into years of 365 days).

        Same convention as the arena's yearly(): a year with fewer than 360 simulated days is scaled to 365."""
        sel = self._sel(year)
        n = int(sel.sum())
        if n == 0:
            raise ValueError(f"no simulated days in {year}")
        tot = self.algos[algo]["profit"][sel].sum(0)                       # $ per MW over the selected days
        if not annualise:
            return tot / 1000.0
        if year is None:                                                   # whole span, expressed per 365 days
            return tot * (365.0 / n) / 1000.0
        return tot * (365.0 / n if n < 360 else 1.0) / 1000.0              # the arena's yearly() rule

    def usd_per_mwh(self, algo, year=None):
        """Margin per MWh discharged (profit after wear / MWh sold) per lane; nan where the lane never discharged."""
        sel = self._sel(year)
        mwh = self.algos[algo]["mwh"][sel].sum(0)
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(mwh > 0, self.algos[algo]["profit"][sel].sum(0) / np.where(mwh > 0, mwh, 1.0), np.nan)

    def cycles_per_day(self, algo, spec_usable_mwh):
        return self.algos[algo]["mwh"].mean(0) / spec_usable_mwh

    def pick_shares(self, lane=None):
        """Share of days the learner followed each expert, (lanes, 6) or (6,) for one lane."""
        k = len(EXPERT_NAMES)
        cnt = np.stack([(self.picks == e).sum(0) for e in range(k)], 1).astype(float)
        sh = cnt / np.maximum(cnt.sum(1, keepdims=True), 1.0)
        return sh if lane is None else sh[self.lane_index(lane)]

    def table(self, year=None):
        """{lane: {A1..A5: $/kW-yr}} for printing and for site tables."""
        cols = {a: self.usd_kw_yr(a, year) for a in N.ALGO_KEYS}
        return {ln: {a: float(cols[a][i]) for a in N.ALGO_KEYS} for i, ln in enumerate(self.lanes)}

    def lane(self, lane):
        i = self.lane_index(lane)
        return {"algos": {a: {f: v[:, i] for f, v in d.items()} for a, d in self.algos.items()}, "picks": self.picks[:, i]}

    def equals(self, other):
        """Exact equality of every ledger field and the picks (lane order and dates included)."""
        if self.lanes != other.lanes or not np.array_equal(self.dates, other.dates):
            return False
        for a in N.ALGO_KEYS:
            for f in N.LEDGER_FIELDS:
                if not np.array_equal(self.algos[a][f], other.algos[a][f]):
                    return False
        return np.array_equal(self.picks, other.picks)

    # ---- persistence
    def save(self, path):
        flat = {f"{a}_{f}": v for a, d in self.algos.items() for f, v in d.items()}
        np.savez_compressed(path, dates=self.dates, lanes=np.array(self.lanes), picks=self.picks,
                            spec=self.spec, context=self.context, **flat)

    @classmethod
    def load(cls, path):
        z = np.load(path, allow_pickle=False)
        algos = {a: {f: z[f"{a}_{f}"] for f in N.LEDGER_FIELDS} for a in N.ALGO_KEYS}
        return cls(list(z["lanes"].astype(str)), z["dates"], str(z["spec"]), str(z["context"]), algos, z["picks"])

    @staticmethod
    def merge_lanes(parts):
        """Concatenate results of separate runs over the same dates, spec and context (different lanes)."""
        p0 = parts[0]
        for p in parts[1:]:
            if not np.array_equal(p.dates, p0.dates) or p.spec != p0.spec or p.context != p0.context:
                raise ValueError("can only merge runs with the same dates, spec and context")
        lanes = sum([p.lanes for p in parts], [])
        if len(set(lanes)) != len(lanes):
            raise ValueError("duplicate lanes across parts")
        algos = {a: {f: np.concatenate([p.algos[a][f] for p in parts], 1) for f in N.LEDGER_FIELDS} for a in N.ALGO_KEYS}
        return LaneResults(lanes, p0.dates, p0.spec, p0.context, algos, np.concatenate([p.picks for p in parts], 1))


# --------------------------------------------------------------------------- workers (module level: picklable)
_G = {}


def _init_worker(ref, dates):
    """Pool initializer: one copy of the shared arrays per worker, one BLAS thread, grid specs registered."""
    _G["ref"], _G["dates"] = ref, dates
    specs.register()
    if threadpool_limits:
        _G["tp"] = threadpool_limits(limits=1)


def _chunk_core(task, ref, dates):
    """One chunk, in whichever process calls it: build the NodalMarket and run the backtest slice."""
    t0 = time.time()
    lanes = task["lanes"]
    P = task["P"]
    if P is None:
        P = task["provider"](lanes)
    market = N.NodalMarket(lanes, P, ref, dates, task["zones"], labels=task.get("labels"), fill_gaps=task.get("fill_gaps", False))
    t1 = time.time()
    res = N.run_slice(market, task["spec"], task["start"], task["end"], context=task["context"])
    res["timing"]["build_market_s"] = t1 - t0
    res["timing"]["peak_rss_mb"] = peak_rss_mb()
    res["timing"]["pid"] = os.getpid()
    res["timing"]["chunk"] = task["chunk_id"]
    return task["chunk_id"], res


def _chunk_worker(task):
    return _chunk_core(task, _G["ref"], _G["dates"])


def _oracle_core(task):
    from arena.battery import Spec, oracle_days
    spec = Spec(task["spec"])
    P = task["P"]                                              # (days, lanes, 96)
    D, L, _ = P.shape
    out = np.zeros((D, L))
    blk = task["day_block"]
    for l in range(L):
        for a in range(0, D, blk):
            out[a:a + blk, l] = oracle_days(P[a:a + blk, l, :], spec)
    return task["chunk_id"], out, {"pid": os.getpid(), "peak_rss_mb": peak_rss_mb()}


def _oracle_worker(task):
    specs.register()
    return _oracle_core(task)


class _ThreadEnv:
    """Set the BLAS thread variables to 1 for the life of a pool (workers are spawned on demand and inherit them)."""

    def __enter__(self):
        self.saved = {k: os.environ.get(k) for k in _THREAD_ENV}
        for k in _THREAD_ENV:
            os.environ[k] = "1"
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _pool(workers, initializer=None, initargs=()):
    """A spawn-context ProcessPoolExecutor (Windows semantics on every OS)."""
    return ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context("spawn"), initializer=initializer,
                               initargs=initargs)


# --------------------------------------------------------------------------- lane providers (picklable)
class ArrayProvider:
    """provider(lane_ids) -> columns of an in-memory (days, lanes, 96) array. Mostly for tests: it pickles the array."""

    def __init__(self, P, lane_ids):
        self.P, self.index = np.asarray(P), {x: i for i, x in enumerate(lane_ids)}

    def __call__(self, lane_ids):
        return np.asarray(self.P[:, [self.index[x] for x in lane_ids], :], np.float64)


class NpyProvider:
    """provider(lane_ids) -> columns of a (days, lanes, 96) .npy file opened with mmap, so a worker reads only the lanes of
    its own chunk (float32 on disk is fine; the columns are returned as float64). `all_lane_ids` names the file's columns."""

    def __init__(self, path, all_lane_ids):
        self.path, self.index = str(path), {x: i for i, x in enumerate(all_lane_ids)}

    def __call__(self, lane_ids):
        mm = np.load(self.path, mmap_mode="r")
        return np.asarray(mm[:, [self.index[x] for x in lane_ids], :], np.float64)


# --------------------------------------------------------------------------- public API
def run_lanes(lane_ids, P=None, *, ref, dates, lane_zone, spec="grid", start=None, end=None, provider=None,
              chunk=None, workers=None, context="lane", labels=None, fill_gaps=False, progress=True,
              cache_dir=None, cache_token=None):
    """Run A1-A5 and the adaptive learner on every lane; return a LaneResults in the order of `lane_ids`.

    lane_ids  list of unique lane names.
    P         (days, lanes, 96) $/MWh over `dates`, or None when `provider` builds each chunk's prices in the worker.
    ref       (days, 96) HB_BUSAVG reference.   dates  (days,) ISO strings, strictly increasing: the WHOLE history the
              models and analogs may use. `start`/`end` (ISO, inclusive) pick the simulated days (default: from
              2015-01-01 to the last date, as the arena).
    lane_zone dict lane -> arena load zone (weather comes from that zone's column), or one zone for all lanes.
    spec      "grid" (1 MW / 2 MWh), "grid4h" (1 MW / 4 MWh), or "home".
    chunk     lanes per task (default DEFAULT_CHUNK_LANES, finer for small jobs). workers: processes (default 8);
              workers=1 runs everything in this process (same numbers, no pool).
    context   "lane" (default, chunk-invariant) or "arena" (the arena's pooled behaviour; only exact for its 8 zones).
    cache_dir resumable runs: every finished chunk is saved there (lanes_<key>.npz) and a rerun with the same inputs loads it instead
              of recomputing. The key hashes the lanes, zones, spec, context, start/end, the date span, the reference and (for P) the
              chunk's prices. With a `provider` the prices are not visible here, so pass `cache_token` (any string that changes when
              the provider's data does); without one caching a provider run raises.
    """
    lanes = [str(x) for x in lane_ids]
    L = len(lanes)
    if (P is None) == (provider is None):
        raise ValueError("pass exactly one of P and provider")
    if P is not None and np.asarray(P).shape[1] != L:
        raise ValueError("P must have one column per lane")
    dates = np.asarray(dates).astype(str)
    if isinstance(lane_zone, str):
        zmap = {x: lane_zone for x in lanes}
    elif isinstance(lane_zone, dict):
        zmap = lane_zone
    else:
        zmap = dict(zip(lanes, lane_zone))
    specs.register()
    workers = DEFAULT_WORKERS if workers is None else int(workers)
    chunks = plan_chunks(L, workers, chunk)

    def make_task(i, a, b):
        return {"chunk_id": i, "lanes": lanes[a:b], "zones": [zmap[x] for x in lanes[a:b]],
                "P": None if P is None else np.ascontiguousarray(np.asarray(P)[:, a:b, :]), "provider": provider,
                "spec": spec, "start": start, "end": end, "context": context, "fill_gaps": fill_gaps,
                "labels": None if labels is None else {x: labels[x] for x in lanes[a:b] if x in labels}}

    t0 = time.time()
    parts, todo, paths_by_id = {}, [], {}
    ref = np.asarray(ref, np.float64)
    cache = None
    if cache_dir is not None:
        cache = Path(cache_dir)
        cache.mkdir(parents=True, exist_ok=True)
        if provider is not None and not cache_token:
            raise ValueError("caching a run that uses a provider needs a cache_token (the prices are not visible to the cache key)")
    ref_digest = hashlib.md5(np.ascontiguousarray(ref).tobytes()).hexdigest()
    n_cached = 0
    for i, (a, b) in enumerate(chunks):
        task = make_task(i, a, b)
        if cache is not None:
            f = cache / f"lanes_{_chunk_cache_key(task, ref_digest, dates, cache_token)}.npz"
            if f.exists():
                parts[i] = _load_chunk(f)
                n_cached += 1
                if progress:
                    print(f"  chunk {i:>3} loaded from cache ({len(parts)}/{len(chunks)})", flush=True)
                continue
            paths_by_id[i] = f
        todo.append(task)

    def done(cid, res):
        parts[cid] = res
        if cache is not None:
            _save_chunk(paths_by_id[cid], res)
        if progress:
            _report(len(parts), len(chunks), t0, res)

    if not todo:
        pass
    elif workers <= 1:
        with _OneThread():
            for task in todo:
                done(*_chunk_core(task, ref, dates))
    else:
        with _ThreadEnv():
            ex = _pool(min(workers, len(todo)), _init_worker, (ref, dates))
            futs = []
            try:
                futs = [ex.submit(_chunk_worker, task) for task in todo]
                for f in as_completed(futs):
                    done(*f.result())                         # a failing chunk raises here, with the worker's traceback
            except BaseException:
                for f in futs:
                    f.cancel()
                raise
            finally:
                ex.shutdown(wait=True, cancel_futures=True)
    wall = time.time() - t0
    first = parts[0]
    algos = {a: {f: np.concatenate([parts[i]["algos"][a][f] for i in range(len(chunks))], 1) for f in N.LEDGER_FIELDS}
             for a in N.ALGO_KEYS}
    picks = np.concatenate([parts[i]["picks"] for i in range(len(chunks))], 1)
    backup = {a: np.concatenate([parts[i]["backup"][a] for i in range(len(chunks))], 1) for a in N.ALGO_KEYS}
    n_days = len(first["dates"])
    computed_lanes = sum(t["lanes"].__len__() for t in todo)
    timing = {"wall_s": wall, "lane_days": L * n_days,
              "lane_days_per_s": (computed_lanes * n_days / wall) if todo else float("nan"),     # lanes computed in this call only
              "workers": workers, "chunk": chunks[0][1] - chunks[0][0], "n_chunks": len(chunks), "n_lanes": L, "n_days": n_days,
              "cached_chunks": n_cached, "chunks": [parts[i]["timing"] for i in range(len(chunks))],
              "peak_worker_rss_mb": max(parts[i]["timing"].get("peak_rss_mb", float("nan")) for i in range(len(chunks)))}
    return LaneResults(lanes, first["dates"], spec, context, algos, picks, backup, timing)


def _chunk_cache_key(task, ref_digest, dates, token):
    meta = {"lanes": task["lanes"], "zones": task["zones"], "spec": task["spec"], "context": task["context"],
            "start": task["start"], "end": task["end"], "dates": [str(dates[0]), str(dates[-1]), int(len(dates))],
            "K": N.K_ANALOG, "fill": bool(task.get("fill_gaps")), "version": 1}
    h = hashlib.md5(json.dumps(meta, sort_keys=True).encode())
    h.update(ref_digest.encode())
    if task["P"] is not None:
        h.update(np.ascontiguousarray(task["P"], np.float64).tobytes())
    else:
        h.update(str(token).encode())
    return h.hexdigest()[:20]


def _save_chunk(path, res):
    tmp = Path(str(path) + ".tmp.npz")
    flat = {f"{a}_{f}": res["algos"][a][f] for a in N.ALGO_KEYS for f in N.LEDGER_FIELDS}
    flat.update({f"backup_{a}": res["backup"][a] for a in N.ALGO_KEYS})
    np.savez_compressed(tmp, dates=res["dates"], lanes=np.array(res["lanes"]), picks=res["picks"], spec=res["spec"],
                        context=res["context"], timing=json.dumps(res["timing"]), **flat)
    os.replace(tmp, path)


def _load_chunk(path):
    z = np.load(path, allow_pickle=False)
    tm = json.loads(str(z["timing"]))
    tm["cached"] = True
    return {"dates": z["dates"].astype(str), "lanes": list(z["lanes"].astype(str)), "spec": str(z["spec"]), "context": str(z["context"]),
            "algos": {a: {f: z[f"{a}_{f}"] for f in N.LEDGER_FIELDS} for a in N.ALGO_KEYS},
            "backup": {a: z[f"backup_{a}"] for a in N.ALGO_KEYS}, "picks": z["picks"], "timing": tm}


def _report(done, total, t0, res):
    tm = res["timing"]
    print(f"  chunk {tm['chunk']:>3} done ({done}/{total}) {tm['n_lanes']} lanes x {tm['n_days']} days "
          f"in {tm['total_s']:.0f}s (setup {tm['models_s'] + tm['analogs_s'] + tm.get('build_market_s', 0):.0f}s, "
          f"rss {tm.get('peak_rss_mb', float('nan')):.0f} MB), elapsed {time.time() - t0:.0f}s", flush=True)


def oracle_lanes(P, spec="grid", *, workers=None, chunk=None, day_block=ORACLE_DAY_BLOCK, progress=False):
    """Perfect-foresight ceiling per lane and day: arena.battery.oracle_days run lane by lane, chunked across processes.

    P: (days, lanes, 96). Returns (days, lanes) $ per day per MW of battery. Day blocks are exact (days are independent),
    so the result equals oracle_days(P[:, l, :], Spec(spec)) lane by lane."""
    P = np.asarray(P, np.float64)
    D, L, _ = P.shape
    specs.register()
    workers = DEFAULT_WORKERS if workers is None else int(workers)
    chunk = int(chunk or DEFAULT_ORACLE_CHUNK_LANES)
    chunks = [(a, min(a + chunk, L)) for a in range(0, L, chunk)]
    tasks = [{"chunk_id": i, "spec": spec, "day_block": day_block, "P": np.ascontiguousarray(P[:, a:b, :])}
             for i, (a, b) in enumerate(chunks)]
    out = np.zeros((D, L))
    t0 = time.time()
    if workers <= 1:
        with _OneThread():
            for t in tasks:
                cid, o, _ = _oracle_core(t)
                a, b = chunks[cid]
                out[:, a:b] = o
    else:
        with _ThreadEnv():
            ex = _pool(min(workers, len(tasks)), None, ())
            try:
                futs = [ex.submit(_oracle_worker, t) for t in tasks]
                for k, f in enumerate(as_completed(futs), 1):
                    cid, o, _ = f.result()
                    a, b = chunks[cid]
                    out[:, a:b] = o
                    if progress:
                        print(f"  oracle chunk {cid} done ({k}/{len(tasks)}) elapsed {time.time() - t0:.0f}s", flush=True)
            finally:
                ex.shutdown(wait=True, cancel_futures=True)
    return out


# --------------------------------------------------------------------------- synthetic nodes (benchmarks and tests)
class SyntheticNodeProvider:
    """provider(lane_ids) -> price columns for synthetic 'nodes': a load-zone price lane scaled, shifted by a daily wiggle and
    given noise. Deterministic per lane. SIM, for throughput benchmarks and tests only: not a model of any real node."""

    def __init__(self, base, lane_ids, base_zone_index, seed=7):
        self.base = np.asarray(base, np.float32)                     # (days, zones, 96)
        self.index = {x: i for i, x in enumerate(lane_ids)}
        self.zone_index = list(base_zone_index)
        self.seed = int(seed)

    def __call__(self, lane_ids):
        D = self.base.shape[0]
        t = np.arange(C_NI)
        cols = []
        for x in lane_ids:
            i = self.index[x]
            g = np.random.default_rng(self.seed * 100003 + i)
            z = self.zone_index[i % len(self.zone_index)]
            scale = g.uniform(0.85, 1.25)
            amp = g.uniform(0, 6.0)
            sig = g.uniform(0.5, 4.0)
            wig = amp * np.sin(2 * np.pi * (t + g.integers(0, 96)) / 96)
            cols.append(self.base[:, z, :].astype(np.float64) * scale + wig[None, :] + g.normal(0, sig, (D, C_NI)))
        return np.stack(cols, 1)


C_NI = 96                                      # REAL: arena intervals a day (arena.config.NI)


# --------------------------------------------------------------------------- equivalence check (used by `compat-test`)
def _equiv_worker(kind, spec, start, end):
    """One pipeline of the equivalence check, in its own process: 'arena' = arena.backtest.simulate_market's steps on the arena's
    Market; 'nodal' = NodalMarket built from the 8 zone lanes with the arena's pooled context; 'lane' = same lanes, lane-safe context."""
    specs.register()
    from arena.data import Market
    if kind == "arena":
        return kind, N.arena_slice(spec, start, end)
    nm = N.NodalMarket.from_market(Market("ERCOT"))
    return kind, N.run_slice(nm, spec, start, end, context="arena" if kind == "nodal" else "lane")


def equivalence_check(spec="grid", start="2024-01-01", end="2024-12-31", include_lane=True, workers=3):
    """Run the arena pipeline and the NodalMarket pipeline on the same slice in parallel processes and compare every ledger
    field and the learner's picks exactly. Also reports how far the lane-safe context moves the numbers. Returns a dict."""
    kinds = ["arena", "nodal"] + (["lane"] if include_lane else [])
    t0 = time.time()
    res = {}
    with _ThreadEnv():
        ex = _pool(min(workers, len(kinds)))
        try:
            futs = [ex.submit(_equiv_worker, k, spec, start, end) for k in kinds]
            for f in as_completed(futs):
                k, r = f.result()
                res[k] = r
                print(f"  {k:6s} pipeline done in {r['timing']['total_s']:.0f}s", flush=True)
        finally:
            ex.shutdown(wait=True, cancel_futures=True)
    out = {"spec": spec, "start": start, "end": end, "n_days": len(res["arena"]["dates"]), "lanes": res["arena"]["lanes"],
           "nodal_equals_arena": N.ledgers_equal(res["arena"], res["nodal"]), "wall_s": time.time() - t0}
    fields = {}
    for a in N.ALGO_KEYS:
        fields[a] = {f: bool(np.array_equal(res["arena"]["algos"][a][f], res["nodal"]["algos"][a][f])) for f in N.LEDGER_FIELDS}
    out["field_equal"] = fields
    out["picks_equal"] = bool(np.array_equal(res["arena"]["picks"], res["nodal"]["picks"]))
    out["usd_kw_yr_arena"] = {a: (res["arena"]["algos"][a]["profit"].sum(0) * (365.0 / out["n_days"]) / 1000.0).round(3).tolist() for a in N.ALGO_KEYS}
    if include_lane:
        out["lane_context_vs_arena_usd_kw_yr"] = {
            a: ((res["lane"]["algos"][a]["profit"].sum(0) - res["arena"]["algos"][a]["profit"].sum(0)) * (365.0 / out["n_days"]) / 1000.0).round(3).tolist()
            for a in N.ALGO_KEYS}
        out["lane_context_identical_to_arena"] = N.ledgers_equal(res["arena"], res["lane"])
    return out
