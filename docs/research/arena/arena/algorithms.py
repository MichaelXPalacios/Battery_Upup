"""The five competing dispatch algorithms (plus the perfect-foresight oracle used only as a ceiling).

Every algorithm sees prices only up to yesterday, plus today's weather (a day-ahead forecast stands in for it)
and yesterday's gas price. All of them run on the same battery and the same engine (battery.run).

  1  Clockwork        fixed daily schedule from the last 4 weeks' average price shape
  2  Forecaster       point forecast of tomorrow's 96 prices -> dynamic programme -> water values,
                      then acts on the real price each interval
  3  Weather-regime   region equations + analog days with similar weather/gas -> stochastic DP;
                      holds extra backup energy before storm or extreme-temperature days
  4  Spike hunter     keeps the battery full and sells only into the top few percent of recent prices
  5  Adaptive learner runs all of the above as shadow batteries and follows, per region, whichever has
                      earned the most recently (exponentially discounted)
"""
import numpy as np

from . import config as C
from .battery import act_on_value, feasible, reward, value_det, value_sdp


class History:
    """Rolling buffer of the last H days of prices per lane (the only price memory an algorithm has)."""

    def __init__(self, init_prices, init_weekend, H=63):
        L = init_prices.shape[1]
        self.H = H
        self.buf = np.zeros((H, L, C.NI))
        self.wk = np.zeros(H, bool)
        self.n = 0
        for pd, w in zip(init_prices[-H:], init_weekend[-H:]):
            self.add(pd, w)

    def add(self, pd, weekend):
        self.buf = np.roll(self.buf, -1, axis=0)
        self.wk = np.roll(self.wk, -1)
        self.buf[-1] = pd
        self.wk[-1] = weekend
        self.n = min(self.n + 1, self.H)

    def last(self, k):
        k = max(1, min(k, self.n))
        return self.buf[-k:]

    def profile(self, weekend, days):
        k = max(1, min(days, self.n))
        b, w = self.buf[-k:], self.wk[-k:]
        sel = w == weekend
        return b[sel].mean(0) if sel.sum() >= 2 else b.mean(0)

    def week_ago(self):
        return self.buf[-7] if self.n >= 7 else self.buf[-1]


class Policy:
    name = "policy"
    short = "P"

    def __init__(self, spec, hist):
        self.spec = spec
        self.hist = hist
        self.reserve_today = None

    def begin_day(self, d, ctx, soc):
        pass

    def act(self, t, p, soc, disc):
        raise NotImplementedError

    def observe(self, t, p):
        """Called after each interval with the real price (only the weather-regime algorithm uses it)."""

    def end_day(self, d, pd, weekend=None):
        self.hist.add(pd, self._wk if weekend is None else weekend)


def _blocks(prof, n):
    """Mean price of every contiguous n-interval block: (L, 96 - n + 1)."""
    cs = np.concatenate([np.zeros((prof.shape[0], 1)), np.cumsum(prof, 1)], 1)
    return (cs[:, n:] - cs[:, :-n]) / n


class Clockwork(Policy):
    name, short = "Clockwork", "A1"

    def begin_day(self, d, ctx, soc):
        self._wk = ctx["weekend"]
        n = max(1, (self.spec.E - self.spec.R) // 2)
        prof = self.hist.profile(ctx["weekend"], 28)
        bm = _blocks(prof, n)                                   # (L, 97-n)
        L = prof.shape[0]
        bd = np.argmax(np.where(np.arange(bm.shape[1]) >= n, bm, -np.inf), 1)
        bc = np.zeros(L, int)
        for i in range(L):
            bc[i] = int(np.argmin(bm[i, :bd[i] - n + 1]))
        self.bd, self.bc, self.n = bd, bc, n

    def act(self, t, p, soc, disc):
        a = np.zeros(len(p), int)
        a = np.where((t >= self.bc) & (t < self.bc + self.n), -2, a)
        a = np.where((t >= self.bd) & (t < self.bd + self.n), 2, a)
        return a


class Forecaster(Policy):
    name, short = "Forecaster", "A2"

    def __init__(self, spec, hist, terminal=1.0):
        super().__init__(spec, hist)
        self.terminal = terminal

    def forecast(self, weekend):
        """Recent-days point forecast, with its swings rescaled to the size of a typical real day.

        Averaging days whose peaks land at slightly different times flattens the average; left alone the
        planner would see smaller spreads than any real day and skip profitable cycles.
        """
        f = 0.5 * self.hist.profile(weekend, 14) + 0.3 * self.hist.last(1)[0] + 0.2 * self.hist.week_ago()
        recent = self.hist.last(14)                                        # (k, L, 96)
        srt = np.sort(recent, axis=2)
        typical = (srt[:, :, -8:].mean(2) - srt[:, :, :8].mean(2)).mean(0)  # mean daily top8-bottom8 spread
        fs = np.sort(f, axis=1)
        own = np.maximum(fs[:, -8:].mean(1) - fs[:, :8].mean(1), 1e-6)
        k = np.clip(typical / own, 1.0, 3.0)[:, None]
        mid = np.median(f, axis=1, keepdims=True)
        return mid + (f - mid) * k

    def w_end(self):
        recent = self.hist.last(7)
        return np.maximum(np.quantile(recent.transpose(1, 0, 2).reshape(recent.shape[1], -1), 0.25, axis=1), 0) \
            / self.spec.rte * self.terminal

    def begin_day(self, d, ctx, soc):
        self._wk = ctx["weekend"]
        self.V = value_det(self.forecast(ctx["weekend"]), self.spec, self.w_end())
        self.reserve_today = None

    def act(self, t, p, soc, disc):
        res = np.full(len(p), self.spec.R) if self.reserve_today is None else self.reserve_today
        return act_on_value(self.V[:, t + 1], p, soc, disc, self.spec, res)


class WeatherRegime(Forecaster):
    """Scenario paths from analog days, re-weighted through the day as real prices arrive.

    Each analog day (a past day with similar weather, gas and recent prices, rescaled for fuel) is a complete
    96-interval price path, so a hot day stays hot all day. A plan (value function) is solved for every path;
    the battery acts on the weighted average, and after each interval the weights move toward the paths that
    have tracked today's real prices best (a Bayesian update), so the algorithm learns which kind of day it is in.
    """
    name, short = "Weather-regime", "A3"

    def begin_day(self, d, ctx, soc):
        self._wk = ctx["weekend"]
        F, Wt = ctx.get("analog_prices"), ctx.get("analog_w")
        base = self.forecast(ctx["weekend"])
        if F is None:
            F2, W2 = base[:, None, :], np.ones((base.shape[0], 1))
        else:
            F2 = np.concatenate([F, base[:, None, :]], 1)                 # recent-days forecast as one more path
            W2 = np.concatenate([Wt * 0.8, np.full((Wt.shape[0], 1), 0.2)], 1)
        L, K, _ = F2.shape
        V = value_det(F2.reshape(L * K, C.NI), self.spec, np.repeat(self.w_end(), K))
        self.Vk = V.reshape(L, K, C.NI + 1, -1).astype(np.float32)
        self.Fk = F2
        self.logw = np.log(np.maximum(W2, 1e-12))
        self.scale = np.maximum(10.0, 0.25 * np.abs(F2).mean(axis=(1, 2)))[:, None]   # $/MWh tolerance per lane
        storm = ctx.get("storm")
        if storm is not None and self.spec.R > 0:
            self.reserve_today = np.where(storm, max(C.STORM_RESERVE_UNITS, self.spec.R), self.spec.R)
        else:
            self.reserve_today = None

    def act(self, t, p, soc, disc):
        w = np.exp(self.logw - self.logw.max(1, keepdims=True))
        w /= w.sum(1, keepdims=True)
        V_next = np.einsum("lk,lks->ls", w, np.where(self.Vk[:, :, t + 1, :] < -1e17, 0.0, self.Vk[:, :, t + 1, :]))
        V_next[:, :self.spec.R] = -1e18
        res = np.full(len(p), self.spec.R) if self.reserve_today is None else self.reserve_today
        return act_on_value(V_next, p, soc, disc, self.spec, res)

    def observe(self, t, p):
        """Bayesian re-weighting of the day's scenario paths after the real price for interval t is seen."""
        self.logw += -0.5 * ((p[:, None] - self.Fk[:, :, t]) / self.scale) ** 2 * 0.25


class SpikeHunter(Policy):
    name, short = "Spike hunter", "A4"

    def __init__(self, spec, hist, q_hi=0.97, q_lo=0.25, window=60):
        super().__init__(spec, hist)
        self.q_hi, self.q_lo, self.window = q_hi, q_lo, window

    def begin_day(self, d, ctx, soc):
        self._wk = ctx["weekend"]
        recent = self.hist.last(self.window)
        flat = recent.transpose(1, 0, 2).reshape(recent.shape[1], -1)
        lo = np.quantile(flat, self.q_lo, axis=1)
        hi = np.quantile(flat, self.q_hi, axis=1)
        self.th_c = lo
        self.th_d = np.maximum(hi, np.maximum(lo, 0) / self.spec.rte + self.spec.deg + 10.0)

    def act(self, t, p, soc, disc):
        a = np.where(p <= self.th_c, -2, 0)
        return np.where(p >= self.th_d, 2, a)


class Adaptive(Policy):
    """Hedge-style learner: shadow-runs every expert, follows the best discounted earner per lane."""
    name, short = "Adaptive learner", "A5"

    def __init__(self, spec, hist_factory, gamma=0.97):
        self.spec = spec
        self.experts = [Clockwork(spec, hist_factory()), Forecaster(spec, hist_factory()),
                        WeatherRegime(spec, hist_factory()), SpikeHunter(spec, hist_factory(), 0.97),
                        SpikeHunter(spec, hist_factory(), 0.90), Forecaster(spec, hist_factory(), terminal=0.7)]
        self.expert_names = ["Clockwork", "Forecaster", "Weather-regime", "Spike hunter q97", "Spike hunter q90",
                             "Forecaster (low carry)"]
        self.gamma = gamma
        self.hist = self.experts[0].hist
        self.L = None
        self.reserve_today = None

    def begin_day(self, d, ctx, soc):
        if self.L is None:
            self.L = len(soc)
            self.soc_e = [np.full(self.L, self.spec.R, int) for _ in self.experts]
            self.score = np.zeros((len(self.experts), self.L))
            self.choice = np.full(self.L, 1, int)                # start with the forecaster
            self.picks = []
        self.day_profit = np.zeros((len(self.experts), self.L))
        self.disc_e = [np.zeros(self.L, int) for _ in self.experts]
        for e in self.experts:
            e.begin_day(d, ctx, soc)
        res = [e.reserve_today if e.reserve_today is not None else np.full(self.L, self.spec.R) for e in self.experts]
        self.res_e = res
        self.reserve_today = np.choose(self.choice, res)
        self.picks.append(self.choice.copy())

    def act(self, t, p, soc, disc):
        acts_real = []
        for k, e in enumerate(self.experts):
            a = e.act(t, p, self.soc_e[k], self.disc_e[k])
            a = feasible(a, self.soc_e[k], self.disc_e[k], self.spec, np.where(a < 0, self.spec.R, self.res_e[k]))
            self.day_profit[k] += reward(p, a, self.spec)
            self.soc_e[k] = self.soc_e[k] - a
            self.disc_e[k] = self.disc_e[k] + np.maximum(a, 0)
            acts_real.append(e.act(t, p, soc, disc))
        return np.choose(self.choice, np.stack(acts_real, 0))

    def observe(self, t, p):
        for e in self.experts:
            e.observe(t, p)

    def end_day(self, d, pd, weekend=None):
        for e in self.experts:
            e.end_day(d, pd)
        self.score = self.gamma * self.score + self.day_profit
        best = np.argmax(self.score, 0)
        cur = self.score[self.choice, np.arange(self.L)]
        # switch only on a clear lead (hysteresis keeps it from flapping on noise)
        lead = self.score[best, np.arange(self.L)] - cur
        self.choice = np.where(lead > 0.05 * np.abs(cur) + 1.0, best, self.choice)


ALGORITHMS = [("A1", "Clockwork"), ("A2", "Forecaster"), ("A3", "Weather-regime"), ("A4", "Spike hunter"),
              ("A5", "Adaptive learner")]


def run_combined(spec, hist_factory, P, days_ctx, forced=None, gamma=0.97):
    """One pass that runs A1-A4 standalone and the adaptive learner A5 on top of them.

    The learner's shadow batteries are exactly the standalone runs (same policy, same prices, same start), so each
    expert plans once per day; per interval it is asked for an action twice: at its own state of charge (its
    standalone battery) and at the learner's state of charge (if the learner currently follows it).
    Returns {short: Ledger-like dict}.
    """
    from .battery import Ledger
    experts = [Clockwork(spec, hist_factory()), Forecaster(spec, hist_factory()), WeatherRegime(spec, hist_factory()),
               SpikeHunter(spec, hist_factory(), 0.97), SpikeHunter(spec, hist_factory(), 0.90),
               Forecaster(spec, hist_factory(), terminal=0.7)]
    D, L, _ = P.shape
    n_e = len(experts)
    socs = [np.full(L, spec.R, int) for _ in range(n_e + 1)]       # last one is the learner
    leds = [Ledger(D, L) for _ in range(n_e + 1)]
    score = np.zeros((n_e, L))
    choice = np.full(L, 1, int)
    picks = np.zeros((D, L), np.int8)
    for d in range(D):
        ctx = days_ctx[d]
        discs = [np.zeros(L, int) for _ in range(n_e + 1)]
        for k, e in enumerate(experts):
            e.begin_day(d, ctx, socs[k])
        res = [e.reserve_today if e.reserve_today is not None else np.full(L, spec.R) for e in experts]
        res_l = np.choose(choice, res)
        picks[d] = choice
        for k in range(n_e + 1):
            leds[k].backup[d] = np.minimum(socs[k], spec.E) * C.U
        pd = P[d]
        for t in range(96):
            p = pd[:, t]
            if forced is not None:
                mask, fa = forced(d, t, None)
            else:
                mask = None
            acts_l = []
            for k, e in enumerate(experts):
                a = e.act(t, p, socs[k], discs[k])
                acts_l.append(e.act(t, p, socs[-1], discs[-1]))
                if mask is not None:
                    a = np.where(mask, fa, a)
                a = feasible(a, socs[k], discs[k], spec, np.where(a < 0, spec.R, res[k]))
                _book(leds[k], d, p, a, spec)
                socs[k] = socs[k] - a
                discs[k] = discs[k] + np.maximum(a, 0)
            a = np.choose(choice, np.stack(acts_l, 0))
            if mask is not None:
                a = np.where(mask, fa, a)
            a = feasible(a, socs[-1], discs[-1], spec, np.where(a < 0, spec.R, res_l))
            _book(leds[-1], d, p, a, spec)
            socs[-1] = socs[-1] - a
            discs[-1] = discs[-1] + np.maximum(a, 0)
            for e in experts:
                e.observe(t, p)
        for e in experts:
            e.end_day(d, pd)
        score = gamma * score + np.stack([leds[k].profit[d] for k in range(n_e)], 0)
        best = np.argmax(score, 0)
        cur = score[choice, np.arange(L)]
        lead = score[best, np.arange(L)] - cur
        choice = np.where(lead > 0.05 * np.abs(cur) + 1.0, best, choice)
    out = {"A1": leds[0], "A2": leds[1], "A3": leds[2], "A4": leds[3], "A5": leds[-1]}
    return out, picks


def _book(led, d, p, a, spec):
    chg = np.where(a < 0, -a, 0) * C.U
    dis = np.where(a > 0, a, 0) * C.U
    led.energy[d] += p * dis - p * chg / spec.rte
    led.wear[d] += spec.deg * dis
    led.mwh_out[d] += dis


def make(short, spec, hist_factory):
    if short == "A1":
        return Clockwork(spec, hist_factory())
    if short == "A2":
        return Forecaster(spec, hist_factory())
    if short == "A3":
        return WeatherRegime(spec, hist_factory())
    if short == "A4":
        return SpikeHunter(spec, hist_factory())
    if short == "A5":
        return Adaptive(spec, hist_factory)
    raise KeyError(short)
