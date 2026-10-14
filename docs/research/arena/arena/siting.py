"""Siting: each algorithm decides, every January, where its next 10 MW go (1 MW at a time).

Each algorithm uses only values it could have known by then: its own dispatch results in every option (a region,
or a region-plus-program site in the forward runs) for the years before the decision. Options are scored
with the algorithm's own rule:

  A1 Clockwork         last year's best option gets everything
  A2 Forecaster        3-year mean plus half the trend; at most 5 MW per option per year
  A3 Weather-regime    5-year mean minus half the standard deviation (risk-adjusted); 50/30/20% to the top three
  A4 Spike hunter      best single year of the last three (chases the tail); 60/40% to the top two
  A5 Adaptive learner  recency-weighted posterior (last 6 years, weights 0.6^age), mean minus a quarter standard
                       deviation, greedy per MW; trusts the latest year after a structural break
  A5ts                 the learner's first design (Thompson sampling), kept as a disclosed comparison
Program options carry MW caps (a co-op deal, a utility program); a full option overflows to the next best.

Group caps (optional; added for the physical-grid study in docs/research/grid). `groups` is a list of
(option_indices, cap_mw). The options in one group share ONE MW cap, because batteries behind the same grid
constraint compete for the same congestion. A full group blocks every one of its members (they overflow to the next
best option), exactly like a full per-option cap. Like `caps`, a group cap is counted across the whole fleet
(cumulative MW installed since the first year of the build), never per year. An option may sit in several groups; all
of them must have room. With groups=None (the default) nothing changes: results are bit-identical to the version
without this parameter (docs/research/grid/tests/test_arena_compat.py proves it).
"""
import numpy as np

RNG = np.random.default_rng(20261003)


def _hist(values, y_idx, n):
    return values[max(0, y_idx - n):y_idx]


def _score(rule, values, y_idx, rng):
    h3, h5 = _hist(values, y_idx, 3), _hist(values, y_idx, 5)
    if rule == "A1":
        return values[y_idx - 1]
    if rule == "A2":
        slope = (h3[-1] - h3[0]) / max(len(h3) - 1, 1) if len(h3) > 1 else 0
        return h3.mean(0) + 0.5 * slope
    if rule == "A3":
        return h5.mean(0) - 0.5 * (h5.std(0) if len(h5) > 1 else 0)
    if rule == "A4":
        return h3.max(0)
    if rule in ("A5", "A5ts"):
        h = _hist(values, y_idx, 6)
        k = len(h)
        wts = 0.6 ** np.arange(k)[::-1]
        wts = wts / wts.sum()
        mu = (h * wts[:, None]).sum(0)
        # posterior spread follows each option's own volatility; a fixed contract payment barely varies, so the
        # learner stops "exploring" away from it (a 5%-of-mean prior keeps a little humility)
        var = (wts[:, None] * (h - mu) ** 2).sum(0) * (1 + 1 / max(k, 1)) + (0.05 * np.abs(mu)) ** 2 / max(k, 1) + 1.0
        brk = (k >= 3) & (h[-1] < mu - 2 * np.sqrt(var))
        mu = np.where(brk, 0.5 * mu + 0.5 * h[-1], mu)
        return mu, np.sqrt(var)
    raise KeyError(rule)


def _norm_groups(groups, n_opt):
    """Validate shared-cap groups: [(option_indices, cap_mw)] -> [(sorted unique int index array, float cap)].

    None or an empty list gives [] (no group logic runs at all). A boolean mask is accepted in place of indices.
    """
    if groups is None or len(groups) == 0:
        return []
    out = []
    for g in groups:
        idx, cap = g
        idx = np.asarray(idx)
        idx = np.flatnonzero(idx) if idx.dtype == bool else np.unique(idx.astype(int).reshape(-1))
        if idx.size and (idx[0] < 0 or idx[-1] >= n_opt):
            raise ValueError(f"group option index out of range 0..{n_opt - 1}: {idx.tolist()}")
        cap = float(cap)
        if np.isnan(cap):
            raise ValueError("group cap is NaN")
        out.append((idx, cap))
    return out


def allocate(rule, values, y_idx, mw=10, avail=None, caps_left=None, rng=RNG, groups=None):
    """values: (years, options) $/MW-yr known so far (rows before y_idx). Returns (options,) MW added this year.

    caps_left: optional (options,) MW still free per option.
    groups: optional list of (option_indices, cap_left_mw), MW still free in each shared-cap group. This is a "left"
    value, the same convention as caps_left (portfolio() passes cap minus the group's fleet so far). Within one call
    the MW already placed in a group count against it, and once a group has less than 1 MW left every member is
    blocked. groups=None or [] leaves the result bit-identical to the version without the parameter.
    """
    n_opt = values.shape[1]
    ok = np.ones(n_opt, bool) if avail is None else np.asarray(avail, bool).copy()
    left = np.full(n_opt, np.inf) if caps_left is None else np.asarray(caps_left, float).copy()
    grp = _norm_groups(groups, n_opt)
    alloc = np.zeros(n_opt)
    if y_idx == 0 or not ok.any():
        return alloc
    sc = _score(rule, values, y_idx, rng)
    shares = {"A1": (1.0,), "A3": (0.5, 0.3, 0.2), "A4": (0.6, 0.4)}
    for _ in range(int(mw)):
        room = ok & (left - alloc >= 1)
        for g_idx, g_left in grp:
            if g_left - alloc[g_idx].sum() < 1:        # the group is full: every member is blocked
                room[g_idx] = False
        if not room.any():
            break
        if rule == "A5":
            # greedy on the recency-weighted, risk-adjusted estimate: every option's value is observable (the arena
            # simulates every region), so exploring has no information value and only costs money
            mu, sd = sc
            pick = np.argmax(np.where(room, mu - 0.25 * sd, -np.inf))
        elif rule == "A5ts":
            # first design, kept for comparison: Thompson sampling draws from the posterior (explores)
            mu, sd = sc
            pick = np.argmax(np.where(room, rng.normal(mu, sd), -np.inf))
        elif rule == "A2":
            s = np.where(room & (alloc < mw / 2), sc, -np.inf)
            if not np.isfinite(s).any():
                s = np.where(room, sc, -np.inf)
            pick = np.argmax(s)
        else:
            order = [k for k in np.argsort(-sc) if room[k]]
            tgt = shares[rule]
            # fill the top options toward their target shares; overflow walks down the ranking
            gaps = [(tgt[i] * mw - alloc[k], k) for i, k in enumerate(order[:len(tgt)])]
            gaps = [g for g in gaps if g[0] > 1e-9]
            pick = max(gaps)[1] if gaps else order[0]
        alloc[pick] += 1
    return alloc


def portfolio(rule, values_known, values_real, y0_idx, n_years, mw_per_year=10, avail=None, caps=None, rng=RNG,
              groups=None):
    """Build a fleet from year y0_idx for n_years; each MW earns values_real[year, option] that year.

    values_known: what the algorithm sees when deciding (its own dispatch results, earlier years only).
    avail: optional (years, options) booleans; caps: optional (options,) MW limits across the whole fleet.
    groups: optional list of (option_indices, cap_mw). The options of a group share one cap on the fleet's total MW
    over the whole build (cumulative, like caps, not per year); a full group blocks all its members. Options may
    belong to several groups. groups=None or [] is bit-identical to the version without the parameter.
    """
    n_opt = values_real.shape[1]
    fleet = np.zeros(n_opt)
    caps = np.full(n_opt, np.inf) if caps is None else np.asarray(caps, float)
    grp = _norm_groups(groups, n_opt)
    if any(c < 0 for _, c in grp):
        raise ValueError("group cap must be >= 0")
    rows, profit = [], []
    for k in range(n_years):
        y = y0_idx + k
        av = None if avail is None else avail[y]
        g_left = [(idx, cap - fleet[idx].sum()) for idx, cap in grp] if grp else None
        fleet = fleet + allocate(rule, values_known, y, mw_per_year, av, caps - fleet, rng, g_left)
        rows.append(fleet.copy())
        profit.append(float((fleet * values_real[y]).sum()))
    fleet_hist = np.array(rows)
    mw_years = fleet_hist.sum()
    return {"fleet": fleet_hist, "profit": np.array(profit), "mw_years": float(mw_years),
            "usd_per_mw_yr": float(np.sum(profit) / mw_years) if mw_years else 0.0}
