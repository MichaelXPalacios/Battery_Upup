"""Battery physics, dynamic programmes and the day-by-day simulation engine.

State of charge (SOC) is an integer number of U-MWh units of stored energy. Each 15-minute interval the
battery takes one action in units: -2 (charge at full power), -1, 0, +1, +2 (discharge at full power).
Charging k units stores k*U MWh and draws k*U/rte MWh from the grid (the loss sits on the charge side, as in
the price studies). Discharging k units sells k*U MWh and pays a wear cost of deg $/MWh.
Constraints: reserve <= SOC <= energy, and at most `cap` units discharged per day (a physical limit; wear is priced).
"""
import numpy as np

from . import config as C

ACTIONS = np.array([-2, -1, 0, 1, 2])
NEG = -1e18


class Spec:
    def __init__(self, key):
        d = C.SPECS[key]
        self.key = key
        self.label = d["label"]
        self.E = d["energy_units"]
        self.R = d["reserve_units"]
        self.rte = d["rte"]
        self.deg = d["deg_usd_mwh"]
        self.cap = int(round((self.E - self.R) * d["cycles_per_day"]))
        self.S = self.E + 1
        self.usable_mwh = (self.E - self.R) * C.U


def reward(p, a, spec):
    """$ for action a (units) at price p ($/MWh). Works elementwise on arrays."""
    a = np.asarray(a)
    chg = np.where(a < 0, -a, 0) * C.U
    dis = np.where(a > 0, a, 0) * C.U
    return -p * chg / spec.rte + (p - spec.deg) * dis


# ----------------------------------------------------------------------------------- oracle (perfect foresight)
def oracle_days(P, spec, reserve=None):
    """Exact best daily profit with full knowledge of the day's prices.

    P: (D, 96) prices for D days of one lane. Each day starts at the reserve; at most `cap` units discharged.
    Returns (D,) $ per day (per MW).
    """
    D = P.shape[0]
    R = spec.R if reserve is None else reserve
    S, Cc = spec.E + 1, spec.cap + 1
    V = np.full((D, S, Cc), NEG)
    V[:, R, 0] = 0.0
    for t in range(C.NI):
        p = P[:, t][:, None, None]
        new = V.copy()
        for k in (1, 2):
            # charge k: (s-k, c) -> (s, c)
            cand = V[:, R:S - k, :] - p * k * C.U / spec.rte
            new[:, R + k:S, :] = np.maximum(new[:, R + k:S, :], cand)
            # discharge k: (s+k, c-k) -> (s, c)
            cand = V[:, R + k:S, :Cc - k] + (p - spec.deg) * k * C.U
            new[:, R:S - k, k:] = np.maximum(new[:, R:S - k, k:], cand)
        V = new
    return V.reshape(D, -1).max(axis=1)


# ----------------------------------------------------------------------------------- value functions (water values)
def value_det(F, spec, w_end, reserve=None):
    """Deterministic backward DP on point forecasts. F: (L, 96); w_end: (L,) $/MWh of stored energy at midnight.

    Returns V: (L, 97, S) - the value of holding s units at the start of interval t (no cycle cap inside).
    """
    L = F.shape[0]
    R = spec.R if reserve is None else reserve
    S = spec.S
    s = np.arange(S)
    V = np.full((L, C.NI + 1, S), NEG)
    V[:, C.NI, R:] = (s[R:] - R) * C.U * np.maximum(w_end, 0)[:, None]
    for t in range(C.NI - 1, -1, -1):
        p = F[:, t][:, None]
        nxt = V[:, t + 1, :]
        best = np.full((L, S), NEG)
        for a in ACTIONS:
            # from s, action a leads to s - a
            lo, hi = max(R, R + a), min(S, S + a)       # s range where s - a is within [R, E]
            if lo >= hi:
                continue
            cand = reward(p, a, spec) + nxt[:, lo - a:hi - a]
            best[:, lo:hi] = np.maximum(best[:, lo:hi], cand)
        V[:, t, :] = best
    return V


def value_sdp(F, W, spec, w_end, reserve=None):
    """Stochastic DP over K price samples per interval. F: (L, K, 96) samples, W: (L, K) weights summing to 1.

    V[t, s] = sum_k W_k * max_a ( r(F_k,t, a) + V[t+1, s-a] ): the price is seen before acting, the future is not.
    """
    L, K, _ = F.shape
    R = spec.R if reserve is None else reserve
    S = spec.S
    s = np.arange(S)
    V = np.full((L, C.NI + 1, S), NEG)
    V[:, C.NI, R:] = (s[R:] - R) * C.U * np.maximum(w_end, 0)[:, None]
    for t in range(C.NI - 1, -1, -1):
        p = F[:, :, t][:, :, None]                     # (L, K, 1)
        nxt = V[:, t + 1, :][:, None, :]               # (L, 1, S)
        best = np.full((L, K, S), NEG)
        for a in ACTIONS:
            lo, hi = max(R, R + a), min(S, S + a)
            if lo >= hi:
                continue
            cand = reward(p, a, spec) + nxt[:, :, lo - a:hi - a]
            best[:, :, lo:hi] = np.maximum(best[:, :, lo:hi], cand)
        V[:, t, :] = np.einsum("lk,lks->ls", W, np.where(best <= NEG / 2, 0.0, best))
        V[:, t, :R] = NEG
    return V


def act_on_value(V_next, p, soc, disc, spec, reserve):
    """Pick the action maximising reward + continuation value. V_next: (L, S); p, soc, disc, reserve: (L,)."""
    L = len(p)
    best_val = np.full(L, NEG)
    best_a = np.zeros(L, int)
    rows = np.arange(L)
    for a in ACTIONS:
        s2 = soc - a
        # charging and idling are always allowed (a raised storm reserve must not block refilling);
        # discharging may not go below the reserve or past the daily cycle cap
        ok = (s2 <= spec.E) & (s2 >= 0)
        if a > 0:
            ok &= (s2 >= reserve) & ((disc + a) <= spec.cap)
        s2c = np.clip(s2, 0, spec.E)
        val = reward(p, a, spec) + V_next[rows, s2c]
        val = np.where(ok, val, NEG)
        better = val > best_val + 1e-9
        best_val = np.where(better, val, best_val)
        best_a = np.where(better, a, best_a)
    return best_a


def feasible(a, soc, disc, spec, reserve):
    """Shrink requested actions until they respect SOC limits, the reserve and the daily cycle cap."""
    a = np.asarray(a, int).copy()
    for _ in range(2):
        s2 = soc - a
        too_low = s2 < reserve
        too_high = s2 > spec.E
        over_cap = (a > 0) & ((disc + a) > spec.cap)
        bad = too_low | too_high | over_cap
        a = np.where(bad, np.sign(a) * (np.abs(a) - 1), a)
    s2 = soc - a
    bad = (s2 < reserve) | (s2 > spec.E) | ((a > 0) & ((disc + a) > spec.cap))
    return np.where(bad, 0, a)


# ----------------------------------------------------------------------------------- engine
class Ledger:
    """Per-lane daily accounting."""

    def __init__(self, n_days, L):
        self.energy = np.zeros((n_days, L))        # $ energy margin (sales - purchases)
        self.wear = np.zeros((n_days, L))          # $ wear cost
        self.mwh_out = np.zeros((n_days, L))       # MWh discharged
        self.backup = np.zeros((n_days, L))        # MWh of stored energy held above zero at the day's peak-risk hour

    @property
    def profit(self):
        return self.energy - self.wear


def run(policy, P, spec, days_ctx, buffer=None, forced=None):
    """Simulate `policy` over D days on L lanes.

    P: (D, L, 96) actual prices. days_ctx: list of per-day context dicts handed to policy.begin_day.
    forced: optional callable (d, t) -> (mask, action) forcing actions on some lanes (program events).
    Returns a Ledger.
    """
    D, L, _ = P.shape
    soc = np.full(L, spec.R, int)
    led = Ledger(D, L)
    for d in range(D):
        disc = np.zeros(L, int)
        policy.begin_day(d, days_ctx[d], soc)
        reserve = getattr(policy, "reserve_today", None)
        reserve = np.full(L, spec.R) if reserve is None else np.maximum(reserve, spec.R)
        led.backup[d] = (np.minimum(soc, spec.E) * C.U)
        pd = P[d]
        for t in range(C.NI):
            p = pd[:, t]
            a = policy.act(t, p, soc, disc)
            if forced is not None:
                mask, fa = forced(d, t, soc)
                if mask is not None:
                    a = np.where(mask, fa, a)
            a = feasible(a, soc, disc, spec, np.where(a < 0, spec.R, reserve))
            chg = np.where(a < 0, -a, 0) * C.U
            dis = np.where(a > 0, a, 0) * C.U
            led.energy[d] += p * dis - p * chg / spec.rte
            led.wear[d] += spec.deg * dis
            led.mwh_out[d] += dis
            soc = soc - a
            disc = disc + np.maximum(a, 0)
            policy.observe(t, p)
        policy.end_day(d, pd)
    return led
