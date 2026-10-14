"""Named constants. Each carries its label: REAL (named source), DERIVED, ASSUMPTION, or PROPOSED (a PRD target
still to confirm). Unverified values live here, never inline."""
import os

from .paths import MLFLOW

# ---- time grid
DT_RT_H = 0.25                 # REAL: ERCOT real-time settlement interval is 15 minutes
DT_SCED_H = 5 / 60             # REAL: SCED runs about every 5 minutes
DT_DAM_H = 1.0                 # REAL: DAM is hourly
TZ_MARKET = "America/Chicago"  # REAL: ERCOT uses Central Prevailing Time

# ---- ancillary services (notes from docs/research/grid/notes/rules.md, RTC+B live 5 Dec 2025)
AS_PRODUCTS = ("REGUP", "REGDN", "RRS", "ECRS", "NSPIN")
AS_DURATION_H = {              # REAL (NPRR1282, PUCT-approved): energy a battery must hold per MW of award
    "REGUP": 0.5, "REGDN": 0.5, "RRS": 0.5, "ECRS": 1.0, "NSPIN": 4.0,
}

# ---- battery defaults (match the research arena so cross-checks line up)
DEFAULT_RTE = 0.85             # ASSUMPTION: grid-scale round trip, as arena SPECS["grid"]
DEFAULT_WEAR_USD_PER_MWH = 20.0  # ASSUMPTION: arena wear cost per MWh discharged
ERCOT_PRICE_CAP = 5000.0       # REAL: system-wide offer cap since 2022 ($/MWh)

# ---- service and safety (PROPOSED values follow the PRD's proposed targets)
MAX_FEATURE_AGE_S = 20 * 60    # DERIVED (D0, 10 Oct 2026): 15 min failed closed on ~0.5% of randomly timed signals because
                               #   ERCOT posts RT price files up to ~8.5 min late (507 s on 8 Oct); older still means no dispatch
CYCLE_P99_TARGET_S = 30.0      # PROPOSED (PRD): p99 of one POST /signal cycle
DATA_FRESHNESS_TARGET_S = 5 * 60  # PROPOSED (PRD): ERCOT publish to online store within one SCED interval
ATTRIBUTION_CLOSURE_MAX = 0.05 # PROPOSED (PRD): unexplained share of the actual-vs-perfect-foresight gap
GUARD_EPS_MW = 1e-6            # ASSUMPTION: numeric tolerance the guard may clip; anything larger is a rejection
SLIPPAGE_TOL = 0.05            # ASSUMPTION: Gate 3, paper vs backtest revenue difference as a share of backtest
HALT_AFTER_BREACHES = 3        # ASSUMPTION: repeated monitoring breaches that set the halt flag automatically
DRIFT_PSI_ALERT = 0.2          # ASSUMPTION: common PSI rule of thumb for a material distribution shift
DRIFT_PSI_HALTS = False        # DERIVED (D1a, 10 Oct 2026): every model feature is a 24 h-7 d rolling statistic, so any
                               #   window of hours or days is one regime against a year-long reference; 87-100% of features
                               #   exceeded PSI 0.2 on in-distribution HB_NORTH windows. PSI alerts, it never halts.
CALIBRATION_WINDOW_INTERVALS = 7 * 96   # ASSUMPTION: rolling window of settled 15-minute intervals for calibration
CALIBRATION_HIT_TOL = 0.25     # DERIVED (D1a false-breach study, holdout 2025-26, 4 hubs): with 0.25 plus the skill rule
                               #   below, 2.1-2.5% of in-distribution windows breach (target < 5%); 0.10 gave 42-46%
SKILL_DEGRADATION_MAX = 0.10   # DERIVED (same study): breach when rolling P50 MAE exceeds the 7-day profile-median
                               #   baseline (feature rt_prof7_med, logged with each forecast) by more than 10%
PINBALL_DEGRADATION_MAX = 0.25 # ASSUMPTION: alert-only; 7-day pinball is heavy-tailed, so it never counts toward a halt
CALIBRATION_EVAL_EVERY_S = 24 * 3600    # ASSUMPTION: evaluate calibration once a day (a 7-day window barely moves per cycle)
CALIBRATION_HALT_AFTER = 2     # ASSUMPTION: consecutive daily calibration breaches that set the halt flag; one breach
                               #   raises an alert and a retrain request

# ---- services
# The shared Redis on :6379 belongs to another application and is full (maxmemory, noeviction), so the platform runs
# its own instance on :6390 with append-only persistence (scripts/redis_dispatch.sh): the halt flag must survive a Redis
# restart, or a restart would silently clear the kill switch. redis-py 8 needs protocol=2 to talk to Redis 3.0.
REDIS_URL = os.environ.get("DISPATCH_REDIS_URL", "redis://127.0.0.1:6390/0")   # 127.0.0.1, not localhost: on this
#   machine localhost tries IPv6 first and costs 2.0 s per new connection, because Redis listens on IPv4 only (I1)
HALT_KEY = "dispatch:halt"                       # value: JSON {reason, set_by, set_utc}
FEATURES_LATEST_KEY = "dispatch:features:latest:{sp}"
MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", f"sqlite:///{(MLFLOW / 'mlflow.db').as_posix()}")
MLFLOW_ARTIFACTS = (MLFLOW / "artifacts").as_posix()
MODEL_NAME = "rt_price_quantiles"
STAGE_ALIASES = ("research", "paper", "live")    # PRD stages, implemented as MLflow registered-model aliases
