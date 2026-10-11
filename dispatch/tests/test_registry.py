"""D1a: MLflow tracking and registry. Every test uses a temp sqlite store; the real store is never touched."""
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dispatch import config
from dispatch.models import baselines as B
from dispatch.models import cv
from dispatch.models import quantile as Q
from dispatch.models import synthetic as S
from dispatch.registry import mlflow_registry as R

SP = "HB_TEST"


# ------------------------------------------------------------------------------------------------ fixtures
@pytest.fixture(scope="module")
def reg(tmp_path_factory):
    d = tmp_path_factory.mktemp("mlflow")
    return R.Registry(tracking_uri="sqlite:///" + (d / "mlflow.db").as_posix(), artifact_root=d / "artifacts",
                      cache_dir=d / "cache")


@pytest.fixture(scope="module")
def tiny():
    return S.fit_tiny_forecaster(SP, n_estimators=25, days=90)


def payload(model=(8.5, 11.0, 0.79), sn1=(10.0, 14.0), sn7=(11.0, 15.0), arena=(9.5, 13.0), pm=(9.2, 12.8), sp=SP):
    """Holdout metrics in the schema promote() reads."""
    m = {"pinball_mean": model[0], "mae_p50": model[1], "coverage_p10_p90": model[2], "n": 1000}
    base = {"seasonal_naive_1d": {"pinball_mean": sn1[0], "mae_p50": sn1[1]},
            "seasonal_naive_7d": {"pinball_mean": sn7[0], "mae_p50": sn7[1]},
            "arena_profile": {"pinball_mean": arena[0], "mae_p50": arena[1]},
            "profile_median_7d": {"pinball_mean": pm[0], "mae_p50": pm[1]}}
    return R.holdout_metrics_payload(sp, m, base)


SNAP = {"kind": "lake_catalog", "sha256": "ab" * 32, "n_items": 2}


def new_version(reg, fc, metrics, name="t"):
    run_id = reg.log_run({"model": "tiny"}, metrics, data_snapshot=SNAP, feature_set="rt_v1/1.1.0",
                         settlement_points=[SP], name=name)
    return reg.register(fc, run_id), run_id


# ------------------------------------------------------------------------------------------------ code and data identity
def test_code_sha256_tracks_content_and_ignores_line_endings(tmp_path):
    pkg = tmp_path / "pkg"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "a.py").write_bytes(b"x = 1\ny = 2\n")
    (pkg / "sub" / "b.py").write_bytes(b"def f():\n    return 3\n")
    (pkg / "sub" / "notes.txt").write_text("not python")
    h1, n = R.code_sha256(pkg)
    assert n == 2
    (pkg / "a.py").write_bytes(b"x = 1\r\ny = 2\r\n")                       # CRLF checkout: same code
    assert R.code_sha256(pkg)[0] == h1
    (pkg / "a.py").write_bytes(b"x = 1\ny = 3\n")
    assert R.code_sha256(pkg)[0] != h1
    (pkg / "a.py").write_bytes(b"x = 1\ny = 2\n")
    (pkg / "__pycache__").mkdir()
    (pkg / "__pycache__" / "junk.py").write_text("ignored")
    assert R.code_sha256(pkg)[0] == h1


def test_code_version_of_this_package_has_git_and_hash_fields():
    cv = R.code_version()
    assert set(cv) >= {"git_sha", "git_dirty", "code_sha256", "code_files"}
    assert len(cv["code_sha256"]) == 64 and cv["code_files"] > 10
    assert cv["code_sha256"] == R.code_sha256()[0]
    assert cv["git_sha"] == "unknown" or len(cv["git_sha"]) == 40
    assert isinstance(cv["git_dirty"], bool)                                  # dispatch/ is untracked: expected True until committed


def test_catalog_snapshot_hashes_the_lines_used(tmp_path):
    lines = [{"source": "ercot", "report_id": "NP4-190-CD", "published_utc": f"2026-10-0{i}T10:00:00Z", "sha256": f"{i}" * 64,
              "path": f"p{i}"} for i in range(1, 6)]
    cat = tmp_path / "catalog.jsonl"
    cat.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    full = R.catalog_snapshot(cat)
    assert full["n_items"] == 5 and full["kind"] == "lake_catalog"
    part = R.catalog_snapshot(cat, published_to="2026-10-04")
    assert part["n_items"] == 3 and part["sha256"] != full["sha256"]
    assert R.catalog_snapshot(cat, report_ids=["NOPE"])["n_items"] == 0
    cat.write_text("\n".join(json.dumps(x) for x in reversed(lines)) + "\n", encoding="utf-8")
    assert R.catalog_snapshot(cat)["sha256"] == full["sha256"]                # order of lines does not matter
    with open(cat, "a", encoding="utf-8") as f:
        f.write(json.dumps({"report_id": "X", "published_utc": "2026-10-09T00:00:00Z", "sha256": "f" * 64}) + "\n")
    assert R.catalog_snapshot(cat)["sha256"] != full["sha256"]


# ------------------------------------------------------------------------------------------------ tracking
def test_log_run_records_params_metrics_data_code_features_and_points(reg):
    snap = {"kind": "lake_catalog", "sha256": "cd" * 32, "n_items": 7}
    rid = reg.log_run({"num_leaves": 63, "nested": {"lr": 0.05}}, {"cv": {"HB_TEST": {"pinball_mean": 4.25}}, "bad": float("nan")},
                      data_snapshot=snap, feature_set="rt_v1/1.1.0", settlement_points=["HB_NORTH", "HB_WEST"], name="unit",
                      tags={"purpose": "test"}, tables={"oof": pd.DataFrame({"a": [1, 2]})})
    run = reg.client.get_run(rid)
    assert run.info.status == "FINISHED"
    t = run.data.tags
    assert t["feature_set"] == "rt_v1/1.1.0" and t["settlement_points"] == "HB_NORTH,HB_WEST" and t["purpose"] == "test"
    assert t["data.sha256"] == snap["sha256"] and t["data.kind"] == "lake_catalog"
    assert t["code.sha256"] == R.code_sha256()[0] and len(t["code.git_sha"]) in (7, 40) or t["code.git_sha"] == "unknown"
    assert t["code.git_dirty"] in ("true", "false")
    assert run.data.params["num_leaves"] == "63" and run.data.params["nested.lr"] == "0.05"
    assert run.data.metrics["cv.HB_TEST.pinball_mean"] == 4.25 and "bad" not in run.data.metrics        # non-finite dropped
    arts = {a.path for a in reg.client.list_artifacts(rid)}
    assert {"data_snapshot.json", "code_version.json", "tables"} <= arts
    assert reg.run_metrics(rid)["cv.HB_TEST.pinball_mean"] == 4.25


def test_failed_run_is_marked_failed(reg):
    with pytest.raises(RuntimeError):
        with reg.start_run("boom") as r:
            rid = r.run_id
            raise RuntimeError("x")
    assert reg.client.get_run(rid).info.status == "FAILED"


# ------------------------------------------------------------------------------------------------ register / load
def test_register_load_round_trip_returns_a_working_quantile_forecaster(reg, tiny):
    fc, X, y, d, h = tiny
    v, rid = new_version(reg, fc, payload())
    info = reg.describe(v)
    assert info["tags"]["kind"] == "lightgbm_quantile" and info["tags"]["settlement_points"] == SP
    assert info["tags"]["feature_set"] == "rt_v1/1.1.0" and info["tags"]["data.sha256"] == SNAP["sha256"]
    assert info["tags"]["code.sha256"] == R.code_sha256()[0] and len(info["tags"]["bundle.sha256"]) == 64
    assert info["run_id"] == rid and info["aliases"] == []
    res = reg.promote(v, "research", by="test")
    assert res.version == v and res.previous_version is None
    got = reg.load("research")
    assert isinstance(got, Q.QuantileForecaster)
    assert got.version == v and got.alias == "research" and got.model_uri.endswith(f"/{v}")
    t = pd.DatetimeIndex(d).unique()[30]
    sub = X[d.to_numpy() == t].copy()
    sub.attrs["settlement_point"] = SP
    a, b = fc.predict(sub, t), got.predict(sub, t)
    pd.testing.assert_frame_equal(a.frame, b.frame)
    assert b.model_version == v and b.settlement_point == SP
    ref = got.reference_frame                                                # D2's drift monitor reads this
    market = {c for c in got.models[SP].spec.feature_names if not Q.is_calendar_feature(c)}
    assert ref is not None and 0 < len(ref) <= 500 and set(ref.columns) == market and "horizon" not in ref.columns
    pd.testing.assert_frame_equal(ref, fc.reference_frame)
    assert got.reference_for(SP).shape == ref.shape
    again = reg.load_version(v)
    assert again.version == v and again.bundle_sha256 == info["tags"]["bundle.sha256"]
    rows = reg.versions()
    assert any(r["version"] == v and "research" in r["aliases"] for r in rows)


def test_load_fails_closed_when_nothing_holds_the_alias(reg, tmp_path):
    with pytest.raises(R.NoModelError, match="live"):
        reg.load("live")
    with pytest.raises(ValueError):
        reg.load("production")
    empty = R.Registry(tracking_uri="sqlite:///" + (tmp_path / "never" / "mlflow.db").as_posix())
    with pytest.raises(R.NoModelError):
        empty.load("paper")
    assert empty.aliases() == {}
    assert not (tmp_path / "never").exists()                                 # a read must not create a store


def test_clear_alias_makes_the_service_fail_closed(reg, tiny):
    fc = tiny[0]
    v, _ = new_version(reg, fc, payload())
    reg.promote(v, "research")
    assert reg.load("research").version == v
    assert reg.clear_alias("research", reason="test", by="me") == v
    with pytest.raises(R.NoModelError):
        reg.load("research")
    assert "cleared.research" in reg.describe(v)["tags"]
    assert reg.clear_alias("research", reason="again") is None


def test_tampered_bundle_is_detected_on_load(reg, tiny, tmp_path):
    fc = tiny[0]
    v, rid = new_version(reg, fc, payload())
    reg.promote(v, "research")
    reg.load("research")                                                     # fills the cache
    shutil.rmtree(reg.cache_dir / reg.model_name / v)                        # force a fresh download
    f = next(reg.artifact_root.rglob(f"{rid}/artifacts/model/artifacts/bundle/models/{SP}/p50.txt"))
    f.write_bytes(f.read_bytes() + b"\n# tampered\n")
    with pytest.raises(R.IntegrityError):
        reg.load("research")


# ------------------------------------------------------------------------------------------------ promotion rules
def test_research_requires_beating_every_baseline(reg, tiny):
    fc = tiny[0]
    # no holdout metrics at all
    v0, _ = new_version(reg, fc, {"cv": {"x": 1.0}})
    with pytest.raises(R.PromotionRefused, match="no holdout metrics"):
        reg.promote(v0, "research")
    # beats the seasonal-naives but not the arena profile on P50 MAE
    v1, _ = new_version(reg, fc, payload(model=(8.5, 13.5, 0.8)))
    with pytest.raises(R.PromotionRefused) as e:
        reg.promote(v1, "research")
    assert any("arena_profile" in r and "mae_p50" in r for r in e.value.reasons)
    assert not any("seasonal_naive" in r for r in e.value.reasons)
    # equal is not better
    v2, _ = new_version(reg, fc, payload(model=(9.5, 11.0, 0.8)))
    with pytest.raises(R.PromotionRefused):
        reg.promote(v2, "research")
    # strictly better than all three: accepted
    v3, _ = new_version(reg, fc, payload(model=(9.0, 12.5, 0.8)))
    assert reg.promote(v3, "research").alias == "research"
    # ... and the 7-day profile median counts too
    v4, _ = new_version(reg, fc, payload(model=(9.0, 12.5, 0.8), pm=(8.9, 12.0)))
    with pytest.raises(R.PromotionRefused) as e4:
        reg.promote(v4, "research")
    assert all("profile_median_7d" in r for r in e4.value.reasons)
    assert reg.aliases()["research"] == v3
    # nothing moved on the refusals
    assert reg.describe(v1)["aliases"] == []


def test_paper_needs_research_first_and_a_clear_margin_over_seasonal_naive(reg, tiny):
    fc = tiny[0]
    # beats everything but only by 1.5% on pinball vs the best seasonal-naive (10.0): research ok, paper not
    v, _ = new_version(reg, fc, payload(model=(9.85, 11.0, 0.8), arena=(10.0, 13.0), pm=(10.0, 13.0)))
    with pytest.raises(R.PromotionRefused, match="research before paper"):
        reg.promote(v, "paper")
    reg.promote(v, "research")
    with pytest.raises(R.PromotionRefused) as e:
        reg.promote(v, "paper")
    assert any("pinball_mean gain over best seasonal-naive" in r and "need >= 3%" in r for r in e.value.reasons)
    # a calibrated and clearly better model passes
    ok, _ = new_version(reg, fc, payload(model=(8.5, 11.0, 0.79)))
    reg.promote(ok, "research")
    res = reg.promote(ok, "paper", by="t")
    assert res.alias == "paper" and reg.aliases()["paper"] == ok
    assert reg.load("paper").version == ok
    # coverage outside the band blocks paper even when the loss is better
    bad_cov, _ = new_version(reg, fc, payload(model=(8.5, 11.0, 0.55)))
    reg.promote(bad_cov, "research")
    with pytest.raises(R.PromotionRefused, match="coverage"):
        reg.promote(bad_cov, "paper")
    assert reg.aliases()["paper"] == ok


def test_live_needs_paper_and_a_recorded_gate3_pass(reg, tiny):
    fc = tiny[0]
    v, _ = new_version(reg, fc, payload())
    with pytest.raises(R.PromotionRefused, match="paper before live"):
        reg.promote(v, "live")
    reg.promote(v, "research")
    reg.promote(v, "paper")
    with pytest.raises(R.PromotionRefused, match="Gate 3"):
        reg.promote(v, "live")
    reg.record_gate3(v, False, evidence="slippage 9% > 5%", by="monitor")
    assert reg.describe(v)["tags"]["gate3.pass"] == "false"
    with pytest.raises(R.PromotionRefused, match="Gate 3"):
        reg.promote(v, "live")
    reg.record_gate3(v, True, evidence="slippage 2.1% <= 5% over 2026-09-01..09-30", by="monitor")
    t = reg.describe(v)["tags"]
    assert t["gate3.pass"] == "true" and t["gate3.by"] == "monitor" and "2.1%" in t["gate3.evidence"]
    res = reg.promote(v, "live", by="oncall", note="first live")
    assert res.alias == "live" and reg.aliases()["live"] == v
    assert reg.load("live").version == v
    hist = json.loads(reg.describe(v)["tags"]["stage_history"])
    assert [h["alias"] for h in hist][-3:] == ["research", "paper", "live"]
    st = reg.status()["live"]
    assert st["version"] == v and st["kind"] == "lightgbm_quantile" and st["gate3_pass"] == "true" and st["settlement_points"] == SP
    assert st["code_sha256"] == R.code_sha256()[0] and len(st["bundle_sha256"]) == 64 and st["promoted"].endswith("by oncall")
    # a second version replaces the alias and the result remembers the old one
    v2, _ = new_version(reg, fc, payload())
    reg.promote(v2, "research")
    reg.promote(v2, "paper")
    reg.record_gate3(v2, True, evidence="ok", by="monitor")
    assert reg.promote(v2, "live").previous_version == v
    # the gate holds even if the Gate 3 tag is forged onto a version that never went through paper
    v3, _ = new_version(reg, fc, payload())
    reg.record_gate3(v3, True, evidence="forged", by="x")
    with pytest.raises(R.PromotionRefused):
        reg.promote(v3, "live")


def test_check_promotion_is_a_side_effect_free_dry_run(reg, tiny):
    fc = tiny[0]
    v, _ = new_version(reg, fc, payload())
    before = dict(reg.aliases())
    chk = reg.check_promotion(v, "paper")
    assert not chk.ok and any("research before paper" in r for r in chk.reasons)
    assert reg.aliases() == before and "promoted.research" not in reg.describe(v)["tags"]
    assert reg.check_promotion(v, "research").ok


def test_baselines_are_servable_at_research_only(reg, tiny):
    fc, X, y, d, h = tiny
    b = B.make_baseline("seasonal_naive_1d").fit(SP, X, y)
    rid = reg.log_run({"model": "seasonal_naive_1d"}, {}, data_snapshot=SNAP, feature_set="rt_v1/1.1.0", settlement_points=[SP])
    v = reg.register(b, rid)
    assert reg.describe(v)["tags"]["kind"] == "baseline"
    reg.promote(v, "research", note="fallback")
    got = reg.load("research")
    assert isinstance(got, B.SeasonalNaive) and got.version == v
    t = pd.DatetimeIndex(d).unique()[10]
    sub = X[d.to_numpy() == t].copy()
    out = got.predict(sub, t, settlement_point=SP)
    assert out.model_version == v and (out.frame["rt_p10"] <= out.frame["rt_p90"]).all()
    with pytest.raises(R.PromotionRefused, match="cannot beat itself"):
        reg.promote(v, "paper")
    with pytest.raises(R.PromotionRefused):
        reg.promote(v, "live")


def test_bad_aliases_and_versions(reg):
    with pytest.raises(ValueError):
        reg.promote("1", "production")
    with pytest.raises(R.NoModelError):
        reg.promote("9999", "research")
    with pytest.raises(R.NoModelError):
        reg.record_gate3("9999", True)


# ------------------------------------------------------------------------------------------------ safety
def test_tests_cannot_write_to_the_real_store():
    real = R.Registry()                                                      # config.MLFLOW_TRACKING_URI
    assert real.is_default_store
    with pytest.raises(R.RegistryError, match="REAL MLflow store"):
        real.log_run({}, {}, data_snapshot=SNAP, feature_set="x", settlement_points=["A"])
    with pytest.raises(R.RegistryError):
        real.promote("1", "research")
    with pytest.raises(R.RegistryError):
        real.clear_alias("research", reason="x")


def test_module_level_api_follows_configure(tmp_path):
    uri = "sqlite:///" + (tmp_path / "m.db").as_posix()
    try:
        r = R.configure(uri)
        assert r.uri == uri and R.default() is r
        with pytest.raises(R.NoModelError):
            R.load("paper")
        assert R.aliases() == {}
        import dispatch.registry as pkg
        assert pkg.load is R.load and pkg.promote is R.promote and pkg.NoModelError is R.NoModelError
    finally:
        R.configure()                                                        # back to config


def test_thresholds_are_named_and_labelled():
    assert R.PAPER_MIN_PINBALL_GAIN > 0 and R.PAPER_MIN_MAE_GAIN > 0 and R.PAPER_COVERAGE_BAND[0] < R.NOMINAL_COVERAGE < R.PAPER_COVERAGE_BAND[1]
    assert R.STAGES == tuple(config.STAGE_ALIASES) == ("research", "paper", "live")
    assert set(R.RESEARCH_BASELINES) == {"seasonal_naive_1d", "seasonal_naive_7d", "arena_profile", "profile_median_7d"}


def test_operator_cli_prints_status_and_reports_refusals(reg, tiny, capsys):
    fc = tiny[0]
    good, _ = new_version(reg, fc, payload())
    bad, _ = new_version(reg, fc, payload(model=(9.9, 13.9, 0.8)))                 # worse than the arena profile on MAE
    try:
        R.configure(reg.uri, artifact_root=reg.artifact_root, cache_dir=reg.cache_dir)
        assert R.main(["promote", bad, "research", "--by", "cli"]) == 1
        assert "REFUSED" in capsys.readouterr().out
        assert R.main(["check", good, "paper"]) == 1                                # not through research yet
        assert "FAIL" in capsys.readouterr().out
        assert R.main(["promote", good, "research", "--by", "cli"]) == 0
        assert f"version {good} now holds research" in capsys.readouterr().out
        assert R.main(["status"]) == 0
        st = json.loads(capsys.readouterr().out)
        assert st["research"]["version"] == good
        assert R.main(["gate3", good, "pass", "--evidence", "ok", "--by", "cli"]) == 0
        assert reg.describe(good)["tags"]["gate3.pass"] == "true"
        assert R.main(["clear", "research", "--reason", "test", "--by", "cli"]) == 0
        assert "NoModel" not in capsys.readouterr().out and reg.aliases().get("research") is None
        assert R.main(["versions"]) == 0
    finally:
        R.configure()


def test_paper_monitors_gate3_result_unlocks_live_through_the_exact_call_d2_makes(reg, tiny):
    """D2: monitoring.checks.record_gate3_result(result, load(alias).version) -> registry.record_gate3(version, passed, evidence=, by=)."""
    checks = pytest.importorskip("dispatch.monitoring.checks")
    fc = tiny[0]
    v, _ = new_version(reg, fc, payload())
    reg.promote(v, "research")
    reg.promote(v, "paper")
    served = reg.load("paper").version                                    # what the service holds, as a string
    good = checks.Gate3Result(passed=True, slippage_ratio=0.021, paper_revenue_usd=980.0, backtest_revenue_usd=1000.0,
                              tol=0.05, n_intervals=2880)
    bad = checks.Gate3Result(passed=False, slippage_ratio=0.09, paper_revenue_usd=910.0, backtest_revenue_usd=1000.0,
                             tol=0.05, n_intervals=2880)
    out = checks.record_gate3_result(bad, served, recorder=reg.record_gate3)
    assert out["recorded"] and out["error"] == "" and reg.describe(v)["tags"]["gate3.pass"] == "false"
    with pytest.raises(R.PromotionRefused, match="Gate 3"):
        reg.promote(v, "live")
    out = checks.record_gate3_result(good, served, recorder=reg.record_gate3)
    t = reg.describe(v)["tags"]
    assert out["recorded"] and t["gate3.pass"] == "true" and t["gate3.by"] == "d2:monitor" and "slippage 0.0210" in t["gate3.evidence"]
    assert reg.promote(v, "live").alias == "live"
    # the module-level function D2 imports by default (default registry), not just the instance method
    v2, _ = new_version(reg, fc, payload())
    try:
        R.configure(reg.uri, artifact_root=reg.artifact_root, cache_dir=reg.cache_dir)
        assert checks._registry_recorder() is R.record_gate3
        assert checks.record_gate3_result(good, v2)["recorded"]
        assert reg.describe(v2)["tags"]["gate3.pass"] == "true"
    finally:
        R.configure()


# ------------------------------------------------------------------------------------------------ holdout metrics for the monitor
def _reference(seed=1, n=6000, sp_shift=0.0):
    rng = np.random.default_rng(seed)
    y = rng.gamma(2.0, 15.0, n)
    q = np.stack([np.full(n, 12.0 + sp_shift), np.full(n, 24.0 + sp_shift), np.full(n, 52.0 + sp_shift)], axis=1)
    return cv.reference_metrics(y, q, rng.integers(1, 97, n), window={
        "start": "2025-01-01T00:00:00+00:00", "end": "2026-09-25T23:45:00+00:00", "decision_every": "4h", "horizons": "1-96", "rows": n})


def test_version_tag_carries_the_holdout_metrics_the_calibration_monitor_reads(reg, tiny):
    fc = tiny[0]
    ref = _reference()
    withm = Q.QuantileForecaster(dict(fc.models), feature_set=fc.feature_set, meta={"holdout": {SP: ref}})
    v, _ = new_version(reg, withm, payload())
    tags = reg.describe(v)["tags"]
    assert R.HOLDOUT_TAG == "holdout.metrics" and json.loads(tags[R.HOLDOUT_TAG]) == withm.holdout_metrics
    assert reg.holdout_metrics(v) == withm.holdout_metrics                                  # readable without loading the bundle
    assert reg.holdout_metrics(v)[SP]["pooled"]["pinball_mean"] == pytest.approx(ref["pooled"]["pinball_mean"])
    got = reg.load_version(v)
    assert got.holdout_metrics == withm.holdout_metrics                                     # and from the loaded Forecaster
    assert got.holdout_for(SP)["hit_p50"] == ref["pooled"]["hit_p50"]
    v0, _ = new_version(reg, fc, payload())                                                 # a bundle registered without them
    assert R.HOLDOUT_TAG not in reg.describe(v0)["tags"] and reg.holdout_metrics(v0) == {}
    with pytest.raises(R.NoModelError):
        reg.holdout_metrics("9999")
    try:                                                                                    # the module-level function follows configure
        R.configure(reg.uri, artifact_root=reg.artifact_root, cache_dir=reg.cache_dir)
        assert R.holdout_metrics(v) == withm.holdout_metrics
    finally:
        R.configure()


def test_holdout_tag_value_fits_four_points_and_degrades_to_valid_json_for_many():
    four = {sp: _reference(seed=i, sp_shift=i) for i, sp in enumerate(("HB_NORTH", "HB_WEST", "HB_HOUSTON", "HB_SOUTH"))}
    s4 = R.holdout_tag_value(four)
    assert len(s4) <= R._TAG_MAX and json.loads(s4) == four                                 # the whole table fits a tag for the four hubs
    many = {f"SP_{i}": _reference(seed=i) for i in range(9)}
    sm = R.holdout_tag_value(many)
    assert len(sm) <= R._TAG_MAX
    got = json.loads(sm)                                                                    # too big: horizon buckets dropped, pooled kept
    assert set(got) == set(many) and all("pooled" in v and "by_horizon" not in v for v in got.values())
    huge = {f"SP_{i}": _reference(seed=i % 5) for i in range(30)}
    sh = R.holdout_tag_value(huge)
    assert len(sh) <= R._TAG_MAX and set(json.loads(sh)["SP_0"]["pooled"]) <= {"n", "pinball_mean", "hit_p10", "hit_p50", "hit_p90"}

