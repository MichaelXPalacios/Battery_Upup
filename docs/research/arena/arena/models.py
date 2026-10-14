"""Region equations: how weather, gas and yesterday's market drive each zone's daily price level, spread and spikes.

For every region and day d the features are
  system weather   CDD_s, HDD_s, hot_s = (Tmax_s - H)+, cold_s = (Cold - Tmin_s)+    (H/Cold per market)
  local weather    the same four terms from the zone's own stations
  fuel             log Henry Hub (yesterday's settle)
  persistence      yesterday's log level, log spread and spike flag
  calendar         two annual harmonics, weekend
and three equations are fitted walk-forward (each year uses only earlier years):
  (1) level     log(mean_d + 30)              ~ OLS (ridge)
  (2) spread    log(top8_d - bottom8_d + 1)   ~ OLS (ridge)
  (3) spike     P(max_d >= threshold)         ~ logistic (ridge)
They feed the weather algorithm, which also draws "analog days": past days with similar weather, gas and
recent prices, rescaled for fuel, as its scenarios for tomorrow.
"""
import numpy as np

from . import config as C

THRESH = {"ERCOT": {"hot": 100.0, "cold": 32.0, "spike": 500.0},
          "NYISO": {"hot": 90.0, "cold": 15.0, "spike": 300.0}}      # ASSUMPTION: thresholds per market

FEATURE_NAMES = ["const", "CDD_sys", "HDD_sys", "hot_sys", "cold_sys", "CDD_loc", "HDD_loc", "hot_loc", "cold_loc",
                 "log_gas", "level_lag", "spread_lag", "spike_lag", "sin1", "cos1", "sin2", "cos2", "weekend"]


def daily_targets(P, market):
    """P: (D, R, 96). Returns level y1, spread y2, spike y3, raw mean, raw spread (all (D, R))."""
    srt = np.sort(P, axis=2)
    mean = P.mean(axis=2)
    spread = srt[:, :, -8:].mean(axis=2) - srt[:, :, :8].mean(axis=2)
    y1 = np.log(np.maximum(mean + 30.0, 1.0))
    y2 = np.log(np.maximum(spread, 0.0) + 1.0)
    y3 = (P.max(axis=2) >= THRESH[market.name]["spike"]).astype(float)
    return y1, y2, y3, mean, spread


def features(market):
    """(D, R, F) feature tensor; lags use day d-1 (the first day repeats itself)."""
    W, th = market.W, THRESH[market.name]
    D, R = market.P.shape[:2]
    y1, y2, y3, _, _ = daily_targets(market.P, market)

    def lag(x):
        return np.concatenate([x[:1], x[:-1]], 0)

    smean = (W["sys_tmax"] + W["sys_tmin"]) / 2
    lmean = (W["tmax"] + W["tmin"]) / 2
    ang = 2 * np.pi * (market.doy - 1) / 365.25
    cols = [
        np.ones((D, R)),
        np.repeat(np.maximum(smean - 65, 0)[:, None], R, 1),
        np.repeat(np.maximum(65 - smean, 0)[:, None], R, 1),
        np.repeat(np.maximum(W["sys_tmax"] - th["hot"], 0)[:, None], R, 1),
        np.repeat(np.maximum(th["cold"] - W["sys_tmin"], 0)[:, None], R, 1),
        np.maximum(lmean - 65, 0), np.maximum(65 - lmean, 0),
        np.maximum(W["tmax"] - th["hot"], 0), np.maximum(th["cold"] - W["tmin"], 0),
        np.repeat(np.log(lag(market.gas))[:, None], R, 1),
        lag(y1), lag(y2), lag(y3),
        np.repeat(np.sin(ang)[:, None], R, 1), np.repeat(np.cos(ang)[:, None], R, 1),
        np.repeat(np.sin(2 * ang)[:, None], R, 1), np.repeat(np.cos(2 * ang)[:, None], R, 1),
        np.repeat(market.weekend.astype(float)[:, None], R, 1),
    ]
    return np.stack(cols, 2), (y1, y2, y3)


def _ridge(X, y, lam=1.0):
    n, f = X.shape
    mu, sd = X[:, 1:].mean(0), X[:, 1:].std(0) + 1e-6
    Z = np.column_stack([np.ones(n), np.clip((X[:, 1:] - mu) / sd, -8, 8)])
    A = Z.T @ Z + lam * np.eye(f)
    A[0, 0] -= lam
    b = np.linalg.solve(A, Z.T @ y)
    return b, mu, sd


LOGIT_LAMBDA_PER_OBS = 0.05   # ASSUMPTION: ridge strength (x n) on standardised features; chosen out of sample


def _logit(X, y, lam=None, iters=50):
    """Ridge logistic regression by Newton steps with step halving (rare-event safe).

    Spike days past the heat/cold thresholds are nearly separable, so an unpenalised fit runs its coefficients
    to infinity and turns overconfident out of sample; a ridge of LOGIT_LAMBDA_PER_OBS x n keeps it calibrated.
    """
    n, f = X.shape
    lam = LOGIT_LAMBDA_PER_OBS * n if lam is None else lam
    mu, sd = X[:, 1:].mean(0), X[:, 1:].std(0) + 1e-6
    Z = np.column_stack([np.ones(n), (X[:, 1:] - mu) / sd])
    Z[:, 1:] = np.clip(Z[:, 1:], -8, 8)
    b = np.zeros(f)
    prior = np.clip(y.mean(), 1e-4, 1 - 1e-4)
    b[0] = np.log(prior / (1 - prior))
    pen = np.r_[0.0, np.full(f - 1, lam)]

    def objective(bb):
        eta = np.clip(Z @ bb, -30, 30)
        return np.sum(y * eta - np.log1p(np.exp(eta))) - 0.5 * np.sum(pen * bb * bb)

    obj = objective(b)
    for _ in range(iters):
        eta = np.clip(Z @ b, -30, 30)
        pr = 1 / (1 + np.exp(-eta))
        w = np.maximum(pr * (1 - pr), 1e-6)
        H = (Z * w[:, None]).T @ Z + np.diag(pen) + 1e-6 * np.eye(f)
        g = Z.T @ (y - pr) - pen * b
        step = np.linalg.solve(H, g)
        t = 1.0
        while t > 1e-4:
            nb = b + t * step
            no = objective(nb)
            if no >= obj - 1e-9:
                break
            t *= 0.5
        b, done = nb, abs(no - obj) < 1e-8
        obj = no
        if done:
            break
    return b, mu, sd


def predict(model, X):
    b, mu, sd = model
    Z = np.column_stack([np.ones(len(X)), np.clip((X[:, 1:] - mu) / sd, -8, 8)])
    return Z @ b


class RegionModels:
    """Walk-forward fits: models[year][region] = (level, spread, spike) trained on all days before `year`."""

    def __init__(self, market, min_days=365):
        self.market = market
        self.X, (self.y1, self.y2, self.y3) = features(market)
        self.models = {}
        for year in sorted(set(market.years)):
            train = np.where(market.years < year)[0]
            if len(train) < min_days:
                continue
            per = []
            for r in range(market.P.shape[1]):
                X = self.X[train, r]
                m1 = _ridge(X, self.y1[train, r])
                m2 = _ridge(X, self.y2[train, r])
                y3 = self.y3[train, r]
                m3 = _logit(X, y3) if 3 <= y3.sum() < len(y3) - 3 else None
                per.append((m1, m2, m3))
            self.models[year] = per

    def spike_prob(self, d, r):
        year = self.market.years[d]
        if year not in self.models or self.models[year][r][2] is None:
            return None
        eta = predict(self.models[year][r][2], self.X[d:d + 1, r])[0]
        return float(1 / (1 + np.exp(-np.clip(eta, -30, 30))))

    def coef_table(self, year=None):
        """Coefficients in natural units for the report (latest model unless a year is given)."""
        year = max(self.models) if year is None else year
        out = []
        for r, z in enumerate(self.market.zones):
            m1, m2, m3 = self.models[year][r]
            row = {"zone": z, "label": C.LABEL[z]}
            for name, m in (("level", m1), ("spread", m2), ("spike", m3)):
                if m is None:
                    continue
                b, mu, sd = m
                nat = b[1:] / sd
                for k, fname in enumerate(FEATURE_NAMES[1:]):
                    row[f"{name}:{fname}"] = float(nat[k])
            out.append(row)
        return out


# ----------------------------------------------------------------------------------- analog days
ANALOG_W = np.array([1.5, 1.5, 1.0, 1.0, 1.0, 1.2, 0.4])


def analog_matrix(market):
    """(D, R, 7) standardised features used to find similar days."""
    W = market.W
    D, R = market.P.shape[:2]
    y1, _, _, _, _ = daily_targets(market.P, market)
    lagy1 = np.concatenate([y1[:1], y1[:-1]], 0)
    cols = [np.repeat(W["sys_tmax"][:, None], R, 1), np.repeat(W["sys_tmin"][:, None], R, 1),
            W["tmax"], W["tmin"], np.repeat(np.log(market.gas)[:, None], R, 1), lagy1,
            np.repeat(market.weekend.astype(float)[:, None], R, 1)]
    A = np.stack(cols, 2)
    mu = A.reshape(-1, A.shape[2]).mean(0)
    sd = A.reshape(-1, A.shape[2]).std(0) + 1e-9
    return (A - mu) / sd * ANALOG_W


def find_analogs(market, K=24, mode="past", window=30):
    """Nearest past days per (day, region): indices (D, R, K) and distances.

    mode="past": only days strictly before day d (walk-forward, for backtests).
    mode="other_years": any day not in day d's own year (for forward scenarios built from history).
    Candidates must lie within +-window days of the same calendar date.
    """
    A = analog_matrix(market)
    D, R, _ = A.shape
    doy = market.doy
    years = market.years
    idx = np.full((D, R, K), -1, int)
    dist = np.full((D, R, K), np.inf)
    for d in range(D):
        dd = np.abs(doy - doy[d])
        dd = np.minimum(dd, 366 - dd)
        if mode == "past":
            cand = np.where((np.arange(D) < d) & (dd <= window))[0]
        else:
            cand = np.where((years != years[d]) & (dd <= window))[0]
        if len(cand) < 3:
            continue
        diff = A[cand] - A[d][None]                     # (n, R, 7)
        dsq = (diff ** 2).sum(2)                        # (n, R)
        k = min(K, len(cand))
        part = np.argpartition(dsq, k - 1, axis=0)[:k]  # (k, R)
        for r in range(R):
            sel = part[:, r]
            order = np.argsort(dsq[sel, r])
            idx[d, r, :k] = cand[sel[order]]
            dist[d, r, :k] = np.sqrt(dsq[sel[order], r])
    return idx, dist


# ----------------------------------------------------------------------------------- what-if effects (for the report)
def feature_row(th, s_tmax, s_tmin, l_tmax, l_tmin, log_gas, y1_lag, y2_lag, spike_lag, doy, weekend):
    """One feature vector in FEATURE_NAMES order, from raw temperatures (deg F) and lags."""
    sm, lm = (s_tmax + s_tmin) / 2, (l_tmax + l_tmin) / 2
    ang = 2 * np.pi * (doy - 1) / 365.25
    return np.array([1.0, max(sm - 65, 0), max(65 - sm, 0), max(s_tmax - th["hot"], 0), max(th["cold"] - s_tmin, 0),
                     max(lm - 65, 0), max(65 - lm, 0), max(l_tmax - th["hot"], 0), max(th["cold"] - l_tmin, 0),
                     log_gas, y1_lag, y2_lag, spike_lag, np.sin(ang), np.cos(ang), np.sin(2 * ang), np.cos(2 * ang),
                     float(weekend)])


def effects_table(market, rm, last_year=None):
    """Plain-language what-ifs per region from the latest walk-forward model.

    Summer baseline: a July weekday at the median July weather and prices of the last five complete years.
    Winter baseline: the same for January. Shocks: +5 F heat (summer), -10 F cold (winter), +50% gas, and a
    spike the day before. Returns one dict per region with spreads in $/MWh and spike chances in %.
    """
    last_year = max(y for y in rm.models if y <= C.BACKTEST_END + 1) if last_year is None else last_year
    th = THRESH[market.name]
    W = market.W
    rows = []
    for r, z in enumerate(market.zones):
        m1, m2, m3 = rm.models[last_year][r]
        out = {"zone": z, "label": C.LABEL[z]}
        for season, month, doy, dT in (("summer", 7, 196, 5.0), ("winter", 1, 15, -10.0)):
            sel = np.where((market.month == month) & (market.years >= last_year - 5) & (market.years < last_year))[0]
            med = lambda a: float(np.median(a[sel]))
            base = dict(s_tmax=med(W["sys_tmax"]), s_tmin=med(W["sys_tmin"]), l_tmax=float(np.median(W["tmax"][sel, r])),
                        l_tmin=float(np.median(W["tmin"][sel, r])), log_gas=float(np.median(np.log(market.gas[sel]))),
                        y1_lag=float(np.median(rm.y1[sel, r])), y2_lag=float(np.median(rm.y2[sel, r])),
                        spike_lag=0.0, doy=doy, weekend=0)

            def pred(**kw):
                b = dict(base, **kw)
                x = feature_row(th, **b)[None, :]
                lvl = float(np.exp(predict(m1, x)[0]) - 30)
                spr = float(np.exp(predict(m2, x)[0]) - 1)
                spk = float(1 / (1 + np.exp(-np.clip(predict(m3, x)[0], -30, 30)))) if m3 is not None else float("nan")
                return lvl, spr, spk

            l0, s0, k0 = pred()
            l1, s1, k1 = pred(s_tmax=base["s_tmax"] + dT, s_tmin=base["s_tmin"] + dT,
                              l_tmax=base["l_tmax"] + dT, l_tmin=base["l_tmin"] + dT)
            out[f"{season}_tmax_f"] = round(base["l_tmax"], 1)
            out[f"{season}_spread_usd"] = round(s0, 1)
            out[f"{season}_spike_pct"] = round(100 * k0, 2)
            out[f"{season}_shock_spread_pct"] = round(100 * (s1 / max(s0, 1e-6) - 1), 1)
            out[f"{season}_shock_spike_pct"] = round(100 * k1, 2)
            if season == "summer":
                lg, sg, _ = pred(log_gas=base["log_gas"] + np.log(1.5))
                _, _, kp = pred(spike_lag=1.0)
                out["gas50_level_pct"] = round(100 * (lg / max(l0, 1e-6) - 1), 1)
                out["gas50_spread_pct"] = round(100 * (sg / max(s0, 1e-6) - 1), 1)
                out["spike_after_spike_pct"] = round(100 * kp, 2)
        rows.append(out)
    return rows
