"""MLflow tracking and the model registry for the dispatch platform (PRD FR6).

  log_run(...)            one tracked run: params, metrics, the data snapshot, the code version, the feature-set version
                          and the settlement points
  register(forecaster)    package a Forecaster as an MLflow pyfunc and create a registered-model version
  promote(version, alias) move a registered-model ALIAS (research, paper, live) to a version, if the gates pass
  load(alias)             the Forecaster behind an alias; raises NoModelError when the alias is unset (fail closed)

STAGES are registered-model aliases on `config.MODEL_NAME`; one version holds each alias at a time.

PROMOTION RULES (thresholds are ASSUMPTIONS, named below, and every decision is written to the version's tags; in every case
the version must also load: bundle downloaded, sha256 verified, forecaster rebuilt):
  research  the model beats EVERY baseline (seasonal-naive 1d and 7d, arena profile, 7-day profile median) on holdout mean pinball loss and on
            P50 MAE at every settlement point it serves. A baseline version may itself hold `research` (it is the
            fallback), and nothing else.
  paper     must have been through research, and beat the best seasonal-naive by at least PAPER_MIN_PINBALL_GAIN on
            pinball and PAPER_MIN_MAE_GAIN on P50 MAE, with P10-P90 coverage inside PAPER_COVERAGE_BAND, at every point.
  live      must have been through paper, still meet the paper thresholds, and carry `gate3.pass == "true"`, a tag that
            the paper-trading monitor (D2) records with `record_gate3` once the paper ledger matches the backtest within
            `config.SLIPPAGE_TOL` over a replayed period (PRD Gate 3).
  Refusals raise PromotionRefused with the reasons. Nothing else moves an alias.

RUN METRICS the gates read (written by train.py): `holdout.<sp>.<metric>` for the model and
`baseline.<name>.holdout.<sp>.<metric>` for each baseline, metric in {pinball_mean, mae_p50, coverage_p10_p90, n}.

TEST SAFETY. Any write (run, registration, promotion) under pytest against the real default store raises, so a test can
never pollute the real registry; construct `Registry(tracking_uri=<temp sqlite>)` or call `configure(...)`. Reads are
allowed. Importing this module does not import mlflow (about 10 s on this machine); the first call that needs it does.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from .. import config, paths
from ..models.bundle import bundle_kind, bundle_sha256

EXPERIMENT = "dispatch/rt_price_quantiles"
STAGES = tuple(config.STAGE_ALIASES)                    # ("research", "paper", "live")
ENTRY_FILE = Path(__file__).with_name("pyfunc_entry.py")

# ---- promotion thresholds. ASSUMPTION unless stated; they are tags on every promotion, so a change is auditable.
RESEARCH_BASELINES = ("seasonal_naive_1d", "seasonal_naive_7d", "arena_profile", "profile_median_7d")   # all must be beaten
SEASONAL_NAIVE = ("seasonal_naive_1d", "seasonal_naive_7d")
RESEARCH_MIN_GAIN = 0.0            # ASSUMPTION: strictly lower holdout pinball and P50 MAE than every baseline
PAPER_MIN_PINBALL_GAIN = 0.03      # ASSUMPTION: >= 3% lower mean pinball than the best seasonal-naive
PAPER_MIN_MAE_GAIN = 0.03          # ASSUMPTION: >= 3% lower P50 MAE than the best seasonal-naive
PAPER_COVERAGE_BAND = (0.70, 0.90)  # ASSUMPTION: P10-P90 coverage within 10 points of the 80% nominal
NOMINAL_COVERAGE = 0.80

_TAG_MAX = 4900
HOLDOUT_TAG = "holdout.metrics"          # version tag: JSON of Forecaster.holdout_metrics, read by D2's calibration monitor


class RegistryError(RuntimeError):
    pass


class NoModelError(RegistryError):
    """No model version holds the alias (or the registry is empty). The service must not dispatch."""


class IntegrityError(RegistryError):
    """The bundle on disk does not match the hash recorded at registration."""


class PromotionRefused(RegistryError):
    def __init__(self, version, alias, checks):
        self.version, self.alias, self.checks = str(version), alias, list(checks)
        self.reasons = [c.detail for c in self.checks if not c.ok]
        super().__init__(f"refused to promote version {version} to {alias!r}: " + "; ".join(self.reasons))


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class PromotionCheck:
    version: str
    alias: str
    checks: list

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def reasons(self) -> list:
        return [c.detail for c in self.checks if not c.ok]


@dataclass
class PromotionResult:
    version: str
    alias: str
    previous_version: Optional[str]
    checks: list


# ---------------------------------------------------------------------------------------------- code and data identity
def _git(args: Sequence[str], cwd: Path) -> Optional[str]:
    try:
        r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def code_sha256(package_dir: Optional[Path] = None) -> tuple:
    """sha256 over every `dispatch/dispatch/**/*.py` (relative path + content, CRLF normalised) and the file count.

    The dispatch folder is untracked in git, so this hash, not the commit, is the identity of the code that trained a model."""
    pkg = Path(package_dir) if package_dir else paths.PKG_DIR
    files = sorted(p for p in pkg.rglob("*.py") if "__pycache__" not in p.parts)
    h = hashlib.sha256()
    for p in files:
        h.update(p.relative_to(pkg).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes().replace(b"\r\n", b"\n"))
        h.update(b"\0")
    return h.hexdigest(), len(files)


def code_version(repo: Optional[Path] = None, package_dir: Optional[Path] = None) -> dict:
    """Git HEAD, a dirty flag scoped to the dispatch folder, and the content hash of the package."""
    repo = Path(repo) if repo else paths.REPO
    pkg = Path(package_dir) if package_dir else paths.PKG_DIR
    sha = _git(["rev-parse", "HEAD"], repo) or "unknown"
    try:
        rel = pkg.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        rel = "."
    status = _git(["status", "--porcelain", "--", rel], repo)
    digest, n = code_sha256(pkg)
    return {"git_sha": sha, "git_dirty": bool(status) if status is not None else True,
            "git_dirty_scope": rel, "code_sha256": digest, "code_files": n}


def file_snapshot(files: Iterable[Path], *, kind: str = "files", extra: Optional[dict] = None) -> dict:
    """Data snapshot of plain input files: name, size and sha256 of each, plus one hash over all of them."""
    items = []
    for f in sorted(Path(p) for p in files):
        h = hashlib.sha256()
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        items.append({"name": f.name, "bytes": f.stat().st_size, "sha256": h.hexdigest()})
    top = hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()
    return {"kind": kind, "sha256": top, "n_items": len(items), "items": items, **(extra or {})}


def catalog_snapshot(catalog: Optional[Path] = None, *, report_ids: Optional[Iterable[str]] = None,
                     published_from: Optional[str] = None, published_to: Optional[str] = None) -> dict:
    """Data snapshot of the lake: a hash of the catalog.jsonl lines a run used (filtered by report id / publish time).

    The catalog is append-only and every line carries the raw file's sha256, so hashing the lines used pins the raw bytes."""
    cat = Path(catalog) if catalog else paths.CATALOG
    want = set(report_ids) if report_ids else None
    lines, per_report = [], {}
    pub = []
    with open(cat, encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            rec = json.loads(raw)
            if want is not None and rec.get("report_id") not in want:
                continue
            p = rec.get("published_utc", "")
            if published_from and p < published_from:
                continue
            if published_to and p >= published_to:
                continue
            lines.append(raw)
            per_report[rec.get("report_id")] = per_report.get(rec.get("report_id"), 0) + 1
            pub.append(p)
    top = hashlib.sha256("\n".join(sorted(lines)).encode("utf-8")).hexdigest()
    return {"kind": "lake_catalog", "sha256": top, "n_items": len(lines), "per_report": per_report,
            "first_published_utc": min(pub) if pub else None, "last_published_utc": max(pub) if pub else None,
            "catalog": str(cat)}


def env_versions() -> dict:
    out = {"python": platform.python_version(), "platform": platform.platform()}
    for pkg in ("lightgbm", "pandas", "numpy", "optuna", "mlflow", "scikit-learn"):
        try:
            from importlib.metadata import version
            out[pkg] = version(pkg)
        except Exception:
            out[pkg] = "unknown"
    return out


# ---------------------------------------------------------------------------------------------- helpers
def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clip(v: Any, n: int = _TAG_MAX) -> str:
    s = v if isinstance(v, str) else json.dumps(v, default=str)
    return s if len(s) <= n else s[: n - 3] + "..."


def holdout_tag_value(hm: Mapping) -> str:
    """JSON for the version tag `holdout.metrics`. A tag value is capped at `_TAG_MAX` characters, so the whole per-point table
    (pooled numbers, horizon buckets, window) is used when it fits, else the pooled numbers and window, else five pooled numbers."""
    def dump(o) -> str:
        return json.dumps(o, sort_keys=True, separators=(",", ":"), default=str)
    full = dump(hm)
    if len(full) <= _TAG_MAX:
        return full
    slim = {sp: {k: v[k] for k in ("pooled", "window") if k in v} for sp, v in hm.items()}
    if len(dump(slim)) <= _TAG_MAX:
        return dump(slim)
    keep = ("n", "pinball_mean", "hit_p10", "hit_p50", "hit_p90")
    return _clip(dump({sp: {"pooled": {k: v.get("pooled", {})[k] for k in keep if k in v.get("pooled", {})}} for sp, v in hm.items()}))


_BAD_KEY = re.compile(r"[^A-Za-z0-9_\-. /]")


def _key(k: str) -> str:
    return _BAD_KEY.sub("_", str(k))


def flatten(d: Mapping, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        kk = f"{prefix}{k}"
        if isinstance(v, Mapping):
            out.update(flatten(v, kk + "."))
        else:
            out[kk] = v
    return out


def holdout_metrics_payload(sp: str, model: Mapping, baselines: Mapping[str, Mapping]) -> dict:
    """The nested metrics dict `promote` reads: model under holdout.<sp>.*, each baseline under baseline.<name>.holdout.<sp>.*.
    Pass it to `Run.log_metrics` / `log_run(metrics=...)` (merge several points with dict.update on the top-level keys)."""
    keep = ("pinball_mean", "mae_p50", "coverage_p10_p90", "n", "pinball_p10", "pinball_p50", "pinball_p90", "bias_p50",
            "spike_mae_p50")
    out = {"holdout": {sp: {k: v for k, v in model.items() if k in keep}}, "baseline": {}}
    for name, m in baselines.items():
        out["baseline"][name] = {"holdout": {sp: {k: v for k, v in m.items() if k in keep}}}
    return out


def merge_payloads(*payloads: Mapping) -> dict:
    """Deep-merge several holdout_metrics_payload dicts (one per settlement point)."""
    out: dict = {}
    for p in payloads:
        for k, v in p.items():
            if isinstance(v, Mapping):
                out[k] = merge_payloads(out.get(k, {}), v)
            else:
                out[k] = v
    return out


def _is_finite(v) -> bool:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return f == f and f not in (float("inf"), float("-inf"))


def _sqlite_path(uri: str) -> Optional[Path]:
    if uri.startswith("sqlite:///"):
        return Path(uri[len("sqlite:///"):].split("?", 1)[0])
    return None


def default_tracking_uri() -> str:
    """env MLFLOW_TRACKING_URI (read now) > config.MLFLOW_TRACKING_URI."""
    return os.environ.get("MLFLOW_TRACKING_URI") or config.MLFLOW_TRACKING_URI


def _mlflow():
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    import mlflow
    return mlflow


class _RetryingClient:
    """Retry any MlflowClient call that fails because the sqlite file is locked by another process (two training processes
    may write to the same store). Everything else is passed through unchanged."""

    def __init__(self, client, tries: int = 8, delay: float = 1.5):
        self._c, self._tries, self._delay = client, tries, delay

    def __getattr__(self, name):
        attr = getattr(self._c, name)
        if not callable(attr):
            return attr
        import time

        def call(*a, **k):
            for i in range(self._tries):
                try:
                    return attr(*a, **k)
                except Exception as e:
                    msg = str(e).lower()
                    if ("locked" in msg or "database is busy" in msg) and i < self._tries - 1:
                        time.sleep(self._delay * (i + 1))
                        continue
                    raise
        return call


@contextlib.contextmanager
def _fast_save():
    """MLflow's save_model scans every installed distribution (about 20 s on this machine) only to warn about version
    mismatches against the requirements it was just given. Skip that warning; the requirements are pinned by us."""
    try:
        import mlflow.utils.environment as env
        orig = env.warn_dependency_requirement_mismatches
        env.warn_dependency_requirement_mismatches = lambda *a, **k: None
    except Exception:
        yield
        return
    try:
        yield
    finally:
        env.warn_dependency_requirement_mismatches = orig


# ---------------------------------------------------------------------------------------------- run handle
class Run:
    """A tracked run. Use as a context manager; on an exception the run is marked FAILED."""

    def __init__(self, registry: "Registry", run_id: str, name: str):
        self.registry, self.run_id, self.name = registry, run_id, name
        self._open = True

    @property
    def _c(self):
        return self.registry.client

    def log_params(self, params: Mapping) -> None:
        from mlflow.entities import Param
        flat = flatten(params)
        items = [Param(_key(k), _clip(v, 5900)) for k, v in flat.items() if v is not None]
        for i in range(0, len(items), 90):
            self._c.log_batch(self.run_id, params=items[i:i + 90])

    def log_metrics(self, metrics: Mapping, step: int = 0) -> None:
        import time
        from mlflow.entities import Metric
        flat = flatten(metrics)
        ts = int(time.time() * 1000)
        items = [Metric(_key(k), float(v), ts, step) for k, v in flat.items() if _is_finite(v)]
        for i in range(0, len(items), 900):
            self._c.log_batch(self.run_id, metrics=items[i:i + 900])

    def set_tags(self, tags: Mapping) -> None:
        from mlflow.entities import RunTag
        items = [RunTag(_key(k), _clip(v)) for k, v in flatten(tags).items() if v is not None]
        for i in range(0, len(items), 90):
            self._c.log_batch(self.run_id, tags=items[i:i + 90])

    def log_dict(self, obj: Any, artifact_file: str) -> None:
        self._c.log_dict(self.run_id, obj, artifact_file)

    def log_text(self, text: str, artifact_file: str) -> None:
        self._c.log_text(self.run_id, text, artifact_file)

    def log_file(self, path: Path, artifact_path: Optional[str] = None) -> None:
        self._c.log_artifact(self.run_id, str(path), artifact_path)

    def log_table(self, df, name: str, artifact_path: str = "tables") -> None:
        """Write a DataFrame as parquet and attach it."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / f"{name}.parquet"
            df.to_parquet(p)
            self._c.log_artifact(self.run_id, str(p), artifact_path)

    def end(self, status: str = "FINISHED") -> None:
        if self._open:
            self._c.set_terminated(self.run_id, status)
            self._open = False

    def __enter__(self) -> "Run":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.end("FAILED" if exc_type else "FINISHED")


# ---------------------------------------------------------------------------------------------- the registry
class Registry:
    """One MLflow tracking store + model registry. `Registry()` uses config (the real store); tests pass a temp URI."""

    def __init__(self, tracking_uri: Optional[str] = None, artifact_root: Optional[Path] = None,
                 cache_dir: Optional[Path] = None, model_name: Optional[str] = None, experiment: Optional[str] = None):
        self.uri = tracking_uri or default_tracking_uri()
        self.is_default_store = self.uri == config.MLFLOW_TRACKING_URI
        db = _sqlite_path(self.uri)
        if artifact_root is not None:
            self.artifact_root = Path(artifact_root)
        elif self.is_default_store:
            self.artifact_root = Path(config.MLFLOW_ARTIFACTS)
        elif db is not None:
            self.artifact_root = db.parent / "artifacts"
        else:
            self.artifact_root = paths.MLFLOW / "artifacts"
        self.cache_dir = Path(cache_dir) if cache_dir else (db.parent / "model_cache" if db else paths.MLFLOW / "model_cache")
        self.model_name = model_name or config.MODEL_NAME
        self.experiment = experiment or EXPERIMENT
        self._client = None

    # ---- plumbing
    @property
    def exists(self) -> bool:
        db = _sqlite_path(self.uri)
        return db.exists() if db is not None else True

    @property
    def client(self):
        if self._client is None:
            mlflow = _mlflow()
            db = _sqlite_path(self.uri)
            if db is not None:
                db.parent.mkdir(parents=True, exist_ok=True)
            self._client = _RetryingClient(mlflow.MlflowClient(tracking_uri=self.uri, registry_uri=self.uri))
        return self._client

    def _guard_write(self) -> None:
        if "PYTEST_CURRENT_TEST" in os.environ and self.is_default_store and not os.environ.get("DISPATCH_TEST_REAL_MLFLOW"):
            raise RegistryError("refusing to write to the REAL MLflow store from a test: build "
                                "Registry(tracking_uri='sqlite:///<tmp>/mlflow.db') or call registry.configure(...)")

    def _experiment_id(self) -> str:
        c = self.client
        exp = c.get_experiment_by_name(self.experiment)
        if exp is not None and exp.lifecycle_stage == "active":
            return exp.experiment_id
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        loc = (self.artifact_root / "experiments" / self.experiment.replace("/", "_")).as_uri()
        try:
            return c.create_experiment(self.experiment, artifact_location=loc)
        except Exception:                                  # another process created it between our get and create
            exp = c.get_experiment_by_name(self.experiment)
            if exp is None:
                raise
            return exp.experiment_id

    def _ensure_model(self) -> None:
        c = self.client
        try:
            c.get_registered_model(self.model_name)
        except Exception:
            try:
                c.create_registered_model(self.model_name, description="ERCOT real-time price quantile forecasters "
                                          "(research / paper / live are aliases). Models only forecast.")
            except Exception:                              # created concurrently
                c.get_registered_model(self.model_name)

    def init_store(self) -> dict:
        """Create the store, the experiment and the registered model if they do not exist (idempotent). Call it once before
        starting several processes that will write to the same sqlite file."""
        self._guard_write()
        exp = self._experiment_id()
        self._ensure_model()
        return {"uri": self.uri, "experiment_id": exp, "model": self.model_name, "artifact_root": str(self.artifact_root)}

    # ---- runs
    def start_run(self, name: str, *, parent: Optional[str] = None, tags: Optional[Mapping] = None,
                  data_snapshot: Optional[Mapping] = None, feature_set: Optional[str] = None,
                  settlement_points: Optional[Sequence[str]] = None) -> Run:
        """Open a run stamped with code version, environment, data snapshot, feature set and settlement points."""
        self._guard_write()
        c = self.client
        t = {}
        if parent:
            t["mlflow.parentRunId"] = parent
        run = c.create_run(self._experiment_id(), tags=t, run_name=name)
        r = Run(self, run.info.run_id, name)
        cv = code_version()
        meta = {"code.git_sha": cv["git_sha"], "code.git_dirty": str(cv["git_dirty"]).lower(),
                "code.sha256": cv["code_sha256"], "code.files": cv["code_files"]}
        if data_snapshot:
            meta.update({"data.kind": data_snapshot.get("kind", ""), "data.sha256": data_snapshot.get("sha256", ""),
                         "data.n_items": data_snapshot.get("n_items", "")})
        if feature_set:
            meta["feature_set"] = feature_set
        if settlement_points:
            meta["settlement_points"] = ",".join(settlement_points)
        meta.update({f"env.{k}": v for k, v in env_versions().items() if k in ("python", "lightgbm", "pandas", "numpy", "optuna", "mlflow")})
        meta.update(tags or {})
        r.set_tags(meta)
        r.log_dict(cv, "code_version.json")
        if data_snapshot:
            r.log_dict(dict(data_snapshot), "data_snapshot.json")
        return r

    def log_run(self, params: Mapping, metrics: Mapping, *, data_snapshot: Mapping, feature_set: str,
                settlement_points: Sequence[str], name: str = "run", tags: Optional[Mapping] = None,
                tables: Optional[Mapping] = None, artifacts: Optional[Mapping] = None,
                parent_run_id: Optional[str] = None) -> str:
        """One-shot tracked run (see module docstring). `tables` maps name -> DataFrame, `artifacts` name -> file path."""
        with self.start_run(name, parent=parent_run_id, tags=tags, data_snapshot=data_snapshot, feature_set=feature_set,
                            settlement_points=settlement_points) as r:
            r.log_params(params)
            r.log_metrics(metrics)
            for n, df in (tables or {}).items():
                r.log_table(df, n)
            for n, p in (artifacts or {}).items():
                r.log_file(Path(p), n)
        return r.run_id

    def run_metrics(self, run_id: str) -> dict:
        return dict(self.client.get_run(run_id).data.metrics)

    def run_tags(self, run_id: str) -> dict:
        return dict(self.client.get_run(run_id).data.tags)

    # ---- registration
    def register(self, forecaster, run_id: Optional[str] = None, *, description: str = "",
                 tags: Optional[Mapping] = None, name: str = "register") -> str:
        """Package `forecaster` as an MLflow pyfunc under run `run_id` (a new run if None) and create a model version.

        Returns the version string. The version's tags copy the run's code / data / feature-set identity and add the
        bundle hash, kind and settlement points, so a version is self-describing."""
        self._guard_write()
        mlflow = _mlflow()
        owns_run = run_id is None
        feature_set = getattr(forecaster, "feature_set", "")
        sps = list(getattr(forecaster, "settlement_points", ()))
        if owns_run:
            run = self.start_run(name, feature_set=feature_set or None, settlement_points=sps or None)
            run_id = run.run_id
        with tempfile.TemporaryDirectory() as td:
            bundle = Path(td) / "bundle"
            forecaster.save(bundle)
            sha = bundle_sha256(bundle)
            kind = bundle_kind(bundle)
            model_dir = Path(td) / "model"
            reqs = [f"{p}=={v}" for p, v in
                    ((k, env_versions()[k]) for k in ("lightgbm", "pandas", "numpy")) if v != "unknown"]
            with _fast_save():
                mlflow.pyfunc.save_model(path=str(model_dir), python_model=str(ENTRY_FILE),
                                         artifacts={"bundle": str(bundle)}, pip_requirements=reqs)
            self.client.log_artifacts(run_id, str(model_dir), artifact_path="model")
        if owns_run:
            run.end()
        rt = self.run_tags(run_id)
        vt = {k: rt[k] for k in ("code.git_sha", "code.git_dirty", "code.sha256", "data.kind", "data.sha256", "feature_set",
                                 "settlement_points") if k in rt}
        vt.update({"kind": kind, "bundle.sha256": sha, "registered_utc": _now(),
                   "feature_set": vt.get("feature_set") or feature_set,
                   "settlement_points": vt.get("settlement_points") or ",".join(sps)})
        hm = getattr(forecaster, "holdout_metrics", None)
        if hm:
            vt[HOLDOUT_TAG] = holdout_tag_value(hm)
        vt.update({k: _clip(v) for k, v in (tags or {}).items()})
        self._ensure_model()
        mv = self.client.create_model_version(self.model_name, source=f"runs:/{run_id}/model", run_id=run_id,
                                              tags={_key(k): _clip(v) for k, v in vt.items() if v not in (None, "")},
                                              description=description or None)
        return str(mv.version)

    # ---- inspection
    def _version(self, version):
        try:
            return self.client.get_model_version(self.model_name, str(version))
        except Exception as e:
            raise NoModelError(f"{self.model_name} has no version {version}: {e}") from e

    def aliases(self) -> dict:
        """alias -> version string for the aliases currently set."""
        if not self.exists:
            return {}
        try:
            rm = self.client.get_registered_model(self.model_name)
        except Exception:
            return {}
        return {a: str(v) for a, v in (rm.aliases or {}).items()}

    def status(self) -> dict:
        """What each alias holds right now, for D2's /health and the audit log. Empty when nothing is promoted."""
        out = {}
        for alias, v in self.aliases().items():
            t = dict(self._version(v).tags or {})
            out[alias] = {"version": v, "kind": t.get("kind", ""), "settlement_points": t.get("settlement_points", ""),
                          "feature_set": t.get("feature_set", ""), "bundle_sha256": t.get("bundle.sha256", ""),
                          "code_sha256": t.get("code.sha256", ""), "data_sha256": t.get("data.sha256", ""),
                          "promoted": t.get(f"promoted.{alias}", ""), "gate3_pass": t.get("gate3.pass", "")}
        return out

    def describe(self, version) -> dict:
        mv = self._version(version)
        held = [a for a, v in self.aliases().items() if v == str(mv.version)]
        return {"version": str(mv.version), "run_id": mv.run_id, "aliases": held, "tags": dict(mv.tags or {}),
                "source": mv.source, "status": mv.status}

    def versions(self) -> list:
        out = self.client.search_model_versions(f"name='{self.model_name}'")
        al = self.aliases()
        rows = []
        for mv in sorted(out, key=lambda m: int(m.version)):
            t = dict(mv.tags or {})
            rows.append({"version": str(mv.version), "kind": t.get("kind", ""), "settlement_points": t.get("settlement_points", ""),
                         "aliases": [a for a, v in al.items() if v == str(mv.version)], "run_id": mv.run_id,
                         "feature_set": t.get("feature_set", "")})
        return rows

    def holdout_metrics(self, version) -> dict:
        """Holdout pinball and hit rates of a version, per settlement point, without loading the bundle: the same dict as
        `load(alias).holdout_metrics` (tag `holdout.metrics`; see `models.cv.reference_metrics` for the keys). `version` is
        `load(alias).version`. {} for a version registered without them (a baseline, a test bundle)."""
        raw = (dict(self._version(version).tags or {})).get(HOLDOUT_TAG)
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError as e:
            raise RegistryError(f"version {version}: tag {HOLDOUT_TAG!r} is not valid JSON") from e

    def holdout_forecasts(self, version, settlement_point: str):
        """The walk-forward forecasts of a version on its holdout period (decision_utc, horizon, y, rt_p10/p50/p90, baseline
        P50s): the table D1b feeds its shadow runs when it replays the period the model never saw. Raises NoModelError if the
        version's release run has no table for that point."""
        import pandas as pd
        mv = self._version(version)
        tags = self.run_tags(mv.run_id) if mv.run_id else {}
        try:
            run_id = json.loads(tags["child_runs_by_sp"])[settlement_point]
        except (KeyError, ValueError) as e:
            raise NoModelError(f"version {version} has no holdout forecasts for {settlement_point!r}") from e
        mlflow = _mlflow()
        with tempfile.TemporaryDirectory() as td:
            p = mlflow.artifacts.download_artifacts(run_id=run_id, artifact_path="tables/holdout_forecasts.parquet",
                                                    dst_path=td, tracking_uri=self.uri)
            return pd.read_parquet(p)

    # ---- loading
    def load(self, alias: str):
        """The Forecaster that holds `alias`. Raises NoModelError if nothing does, so the service fails closed."""
        if alias not in STAGES:
            raise ValueError(f"alias must be one of {STAGES}, got {alias!r}")
        if not self.exists:
            raise NoModelError(f"no MLflow registry at {self.uri}; nothing holds the {alias!r} alias")
        try:
            mv = self.client.get_model_version_by_alias(self.model_name, alias)
        except Exception as e:
            raise NoModelError(f"no model version holds the {alias!r} alias on {self.model_name!r}") from e
        fc = self._load_version(mv)
        fc.alias = alias
        return fc

    def load_version(self, version):
        return self._load_version(self._version(version))

    def _load_version(self, mv):
        mlflow = _mlflow()
        ver = str(mv.version)
        local = self.cache_dir / f"{self.model_name}" / ver
        marker = local / ".verified"
        want = (mv.tags or {}).get("bundle.sha256")
        if not marker.exists():
            if local.exists():
                shutil.rmtree(local, ignore_errors=True)
            local.mkdir(parents=True, exist_ok=True)
            try:
                got = mlflow.artifacts.download_artifacts(artifact_uri=mv.source, dst_path=str(local), tracking_uri=self.uri,
                                                          registry_uri=self.uri)
            except Exception as e:
                raise NoModelError(f"cannot download version {ver} from {mv.source}: {e}") from e
            if Path(got).resolve() != (local / "model").resolve() and not (local / "model").exists():
                shutil.move(str(got), str(local / "model"))
            (local / "sha256.txt").write_text(want or "", encoding="utf-8")
        bundle = local / "model" / "artifacts" / "bundle"
        if want and bundle_sha256(bundle) != want:
            shutil.rmtree(local, ignore_errors=True)
            raise IntegrityError(f"bundle of version {ver} does not match its recorded sha256 {want[:12]}")
        marker.write_text(_now(), encoding="utf-8")
        pm = mlflow.pyfunc.load_model(str(local / "model"))
        fc = pm.unwrap_python_model().forecaster
        fc.version = ver
        fc.run_id = mv.run_id
        fc.registry_name = self.model_name
        fc.model_uri = f"models:/{self.model_name}/{ver}"
        fc.bundle_sha256 = want
        return fc

    # ---- promotion
    def _policy_checks(self, alias: str, tags: Mapping, metrics: Mapping) -> list:
        kind = tags.get("kind", "")
        sps = [s for s in (tags.get("settlement_points") or "").split(",") if s]
        checks: list = []
        if kind == "baseline":
            if alias == "research":
                return [Check("baseline_fallback", True, "a baseline may hold research as the fallback model")]
            return [Check("baseline_not_promotable", False,
                          f"a baseline cannot be promoted to {alias}: it cannot beat itself")]
        if not sps:
            return [Check("settlement_points", False, "version has no settlement_points tag")]
        for sp in sps:
            m = {k: metrics.get(f"holdout.{sp}.{k}") for k in ("pinball_mean", "mae_p50", "coverage_p10_p90", "n")}
            base = {b: {k: metrics.get(f"baseline.{b}.holdout.{sp}.{k}") for k in ("pinball_mean", "mae_p50")}
                    for b in RESEARCH_BASELINES}
            if any(m[k] is None for k in ("pinball_mean", "mae_p50")):
                checks.append(Check(f"{sp}:evidence", False, f"{sp}: no holdout metrics on the run"))
                continue
            missing = [b for b, v in base.items() if v["pinball_mean"] is None or v["mae_p50"] is None]
            if missing:
                checks.append(Check(f"{sp}:baselines", False, f"{sp}: no holdout metrics for baselines {missing}"))
                continue
            # research: beat every baseline
            for b, v in base.items():
                for k in ("pinball_mean", "mae_p50"):
                    gain = (v[k] - m[k]) / v[k] if v[k] else float("nan")
                    ok = gain > RESEARCH_MIN_GAIN
                    checks.append(Check(f"{sp}:research:{k}:{b}", ok,
                                        f"{sp}: {k} {m[k]:.3f} vs {b} {v[k]:.3f} (gain {gain:+.1%}; need > {RESEARCH_MIN_GAIN:.0%})"))
            if alias in ("paper", "live"):
                best = {k: min(base[b][k] for b in SEASONAL_NAIVE) for k in ("pinball_mean", "mae_p50")}
                for k, need in (("pinball_mean", PAPER_MIN_PINBALL_GAIN), ("mae_p50", PAPER_MIN_MAE_GAIN)):
                    gain = (best[k] - m[k]) / best[k] if best[k] else float("nan")
                    checks.append(Check(f"{sp}:paper:{k}", gain >= need,
                                        f"{sp}: {k} gain over best seasonal-naive {gain:+.1%}; need >= {need:.0%}"))
                cov = m["coverage_p10_p90"]
                lo, hi = PAPER_COVERAGE_BAND
                checks.append(Check(f"{sp}:paper:coverage", cov is not None and lo <= cov <= hi,
                                    f"{sp}: P10-P90 coverage {cov if cov is None else round(cov, 3)}; need within [{lo}, {hi}]"))
        if alias in ("paper", "live"):
            checks.append(Check("stage_order:research", "promoted.research" in tags,
                                "must be promoted to research before paper" if "promoted.research" not in tags else "passed research"))
        if alias == "live":
            checks.append(Check("stage_order:paper", "promoted.paper" in tags,
                                "must be promoted to paper before live" if "promoted.paper" not in tags else "passed paper"))
            g3 = str(tags.get("gate3.pass", "")).lower() == "true"
            checks.append(Check("gate3", g3, "Gate 3 pass flag recorded on the version" if g3
                                else "no Gate 3 pass flag on the version (record_gate3 by the paper monitor)"))
        return checks

    def check_promotion(self, version, alias: str) -> PromotionCheck:
        """Dry run of `promote`: the same checks, no side effects."""
        if alias not in STAGES:
            raise ValueError(f"alias must be one of {STAGES}, got {alias!r}")
        mv = self._version(version)
        tags = dict(mv.tags or {})
        metrics = self.run_metrics(mv.run_id) if mv.run_id else {}
        return PromotionCheck(str(mv.version), alias, self._policy_checks(alias, tags, metrics))

    def promote(self, version, alias: str, *, by: str = "", note: str = "") -> PromotionResult:
        """Point `alias` at `version` if every gate passes; otherwise raise PromotionRefused with the reasons."""
        self._guard_write()
        res = self.check_promotion(version, alias)
        if res.ok:                                       # an alias must never point at a model the service cannot load
            try:
                self._load_version(self._version(res.version))
                res.checks.append(Check("loadable", True, "bundle downloaded, hash verified, forecaster rebuilt"))
            except Exception as e:
                res.checks.append(Check("loadable", False, f"version {res.version} cannot be loaded: {type(e).__name__}: {e}"[:300]))
        if not res.ok:
            raise PromotionRefused(res.version, alias, res.checks)
        prev = self.aliases().get(alias)
        self.client.set_registered_model_alias(self.model_name, alias, res.version)
        mv = self._version(res.version)
        hist = json.loads((mv.tags or {}).get("stage_history", "[]"))
        hist.append({"alias": alias, "utc": _now(), "by": by, "note": note, "previous": prev})
        self.client.set_model_version_tag(self.model_name, res.version, f"promoted.{alias}", f"{_now()} by {by or 'unknown'}")
        self.client.set_model_version_tag(self.model_name, res.version, "stage_history", _clip(hist[-12:]))
        self.client.set_model_version_tag(self.model_name, res.version, f"promotion.{alias}.policy", _clip({
            "research_min_gain": RESEARCH_MIN_GAIN, "paper_min_pinball_gain": PAPER_MIN_PINBALL_GAIN,
            "paper_min_mae_gain": PAPER_MIN_MAE_GAIN, "paper_coverage_band": PAPER_COVERAGE_BAND,
            "checks_passed": sum(c.ok for c in res.checks)}))
        return PromotionResult(res.version, alias, prev, res.checks)

    def record_gate3(self, version, passed: bool, *, evidence: str = "", by: str = "") -> None:
        """Record the PRD Gate 3 outcome on a version (called by the paper monitor, D2). `live` needs a true flag."""
        self._guard_write()
        mv = self._version(version)
        v = str(mv.version)
        for k, val in (("gate3.pass", "true" if passed else "false"), ("gate3.utc", _now()), ("gate3.by", by or "unknown"),
                       ("gate3.evidence", _clip(evidence, 1000))):
            self.client.set_model_version_tag(self.model_name, v, k, val)

    def clear_alias(self, alias: str, *, reason: str, by: str = "") -> Optional[str]:
        """Remove an alias so `load(alias)` raises NoModelError (the service then fails closed). Returns the old version."""
        self._guard_write()
        if alias not in STAGES:
            raise ValueError(f"alias must be one of {STAGES}, got {alias!r}")
        prev = self.aliases().get(alias)
        if prev is None:
            return None
        self.client.delete_registered_model_alias(self.model_name, alias)
        self.client.set_model_version_tag(self.model_name, prev, f"cleared.{alias}", _clip(f"{_now()} by {by or 'unknown'}: {reason}"))
        return prev


# ---------------------------------------------------------------------------------------------- module-level API
_default: Optional[Registry] = None


def configure(tracking_uri: Optional[str] = None, **kw) -> Registry:
    """Set the registry the module-level functions use (tests pass a temp sqlite URI). No arguments: back to config."""
    global _default
    _default = Registry(tracking_uri, **kw) if (tracking_uri or kw) else None
    return default()


def default() -> Registry:
    global _default
    if _default is None:
        _default = Registry()
    return _default


def load(alias: str):
    """DESIGN.md: `registry.load(alias) -> Forecaster`. Raises NoModelError when the alias is unset."""
    return default().load(alias)


def promote(version, alias: str, **kw) -> PromotionResult:
    """DESIGN.md: `registry.promote(version, alias)`; alias is research, paper or live. Raises PromotionRefused."""
    return default().promote(version, alias, **kw)


def register(forecaster, run_id: Optional[str] = None, **kw) -> str:
    return default().register(forecaster, run_id, **kw)


def log_run(*a, **kw) -> str:
    return default().log_run(*a, **kw)


def record_gate3(version, passed: bool, **kw) -> None:
    return default().record_gate3(version, passed, **kw)


def check_promotion(version, alias: str) -> PromotionCheck:
    return default().check_promotion(version, alias)


def holdout_forecasts(version, settlement_point: str):
    return default().holdout_forecasts(version, settlement_point)


def holdout_metrics(version) -> dict:
    return default().holdout_metrics(version)


def aliases() -> dict:
    return default().aliases()


def status() -> dict:
    return default().status()


# ---------------------------------------------------------------------------------------------- command line
def main(argv: Optional[Sequence[str]] = None) -> int:
    """python -m dispatch.registry.mlflow_registry status | versions | check V ALIAS | promote V ALIAS | gate3 V pass|fail |
    clear ALIAS. Every write is attributable (--by) and every refusal prints its reasons."""
    import argparse
    ap = argparse.ArgumentParser(description=main.__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("versions")
    for name in ("check", "promote"):
        p = sub.add_parser(name)
        p.add_argument("version")
        p.add_argument("alias", choices=STAGES)
        p.add_argument("--by", default="")
        p.add_argument("--note", default="")
    g = sub.add_parser("gate3")
    g.add_argument("version")
    g.add_argument("outcome", choices=["pass", "fail"])
    g.add_argument("--evidence", default="")
    g.add_argument("--by", default="")
    c = sub.add_parser("clear")
    c.add_argument("alias", choices=STAGES)
    c.add_argument("--reason", required=True)
    c.add_argument("--by", default="")
    a = ap.parse_args(argv)
    r = default()
    if a.cmd == "status":
        print(json.dumps(r.status(), indent=1))
    elif a.cmd == "versions":
        for row in r.versions():
            print(json.dumps(row))
    elif a.cmd == "check":
        res = r.check_promotion(a.version, a.alias)
        for ck in res.checks:
            print(("PASS " if ck.ok else "FAIL ") + ck.detail)
        return 0 if res.ok else 1
    elif a.cmd == "promote":
        try:
            res = r.promote(a.version, a.alias, by=a.by, note=a.note)
        except PromotionRefused as e:
            print("REFUSED: " + "; ".join(e.reasons))
            return 1
        print(f"version {res.version} now holds {res.alias} (previous: {res.previous_version})")
    elif a.cmd == "gate3":
        r.record_gate3(a.version, a.outcome == "pass", evidence=a.evidence, by=a.by)
        print(f"gate3 {a.outcome} recorded on version {a.version}")
    elif a.cmd == "clear":
        print(f"cleared {a.alias}; it held version {r.clear_alias(a.alias, reason=a.reason, by=a.by)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
