"""Proof that the `groups` hook in arena/siting.py changes nothing when it is not used, and that group caps bind.

Part 1 (backward compatibility). The untouched backup copy of siting.py (taken before this work) is imported by file
path and compared with the live file on randomised cases covering every rule (A1, A2, A3, A4, A5, A5ts), with and
without `avail` and `caps`, for both `allocate` and `portfolio`, with fixed seeds. Equality is exact (np.array_equal,
no tolerance).

Part 2 (group caps). A group is a set of options that share one MW cap; a full group blocks every member. The cap is
cumulative over the fleet (like `caps`), not per year.

Run:  python tests/test_arena_compat.py        (from docs/research/grid, research venv)
"""
import importlib.util
import inspect
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _harness_3a as H  # noqa: E402  (adds the grid dir to sys.path)

import numpy as np  # noqa: E402

from gridsite import paths  # noqa: E402

BACKUP_ARENA = paths.ROOT / "_backup_docs_research_20261010" / "arena" / "arena"
LIVE_ARENA = paths.ARENA_DIR / "arena"
RULES = ["A1", "A2", "A3", "A4", "A5", "A5ts"]
N_SEEDS_ALLOCATE = 40          # per (rule, avail, caps) cell -> 6 * 2 * 2 * 40 = 960 allocate cases
N_SEEDS_PORTFOLIO = 30         # per cell -> 720 portfolio cases


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fresh():
    """Two fresh module objects (own module-level RNG state), loaded by file path, never through `import arena`."""
    return _load(BACKUP_ARENA / "siting.py", "siting_backup_20261010"), _load(LIVE_ARENA / "siting.py", "siting_live")


# ----------------------------------------------------------------------------------- random inputs
def _values(rng, years, n_opt):
    """$/MW-yr history: several shapes, including ties and negatives, so every tie-break and branch is exercised."""
    kind = rng.integers(0, 7)
    base = rng.normal(40, 25, size=(1, n_opt))
    if kind == 0:                                   # plain noise around option means
        v = base + rng.normal(0, 10, size=(years, n_opt))
    elif kind == 1:                                 # trending options (A2 slope, A5 break)
        v = base + np.outer(np.arange(years), rng.normal(0, 4, size=n_opt)) + rng.normal(0, 3, size=(years, n_opt))
    elif kind == 2:                                 # heavy tails
        v = rng.lognormal(3.0, 1.0, size=(years, n_opt)) - 20
    elif kind == 3:                                 # integers: many exact ties
        v = rng.integers(0, 6, size=(years, n_opt)).astype(float) * 10
    elif kind == 4:                                 # duplicated columns: argmax/argsort tie-breaks
        v = rng.normal(30, 10, size=(years, 1)) + np.zeros((years, n_opt))
    elif kind == 5:                                 # a structural break in the last year
        v = base + rng.normal(0, 5, size=(years, n_opt))
        v[-1] = v[-1] * rng.uniform(-0.5, 0.3)
    else:                                           # all negative
        v = -np.abs(base) - rng.uniform(0, 30, size=(years, n_opt))
    if rng.random() < 0.3 and n_opt > 1:            # a fixed-payment option (zero volatility)
        v[:, rng.integers(0, n_opt)] = float(rng.integers(20, 80))
    return v


def _avail(rng, n_opt, shape=None):
    shape = (n_opt,) if shape is None else shape + (n_opt,)
    p = rng.choice([0.0, 0.3, 0.7, 1.0])
    a = rng.random(shape) < p
    return a


def _caps(rng, n_opt):
    c = rng.choice([0.0, 1.0, 2.5, 4.0, 7.0, 15.0, np.inf], size=n_opt)
    return c


def _same(a, b):
    return np.array_equal(np.asarray(a), np.asarray(b), equal_nan=True)


# ----------------------------------------------------------------------------------- part 1: compatibility
def test_signature_is_backward_compatible():
    old, new = _fresh()
    for fn in ("allocate", "portfolio"):
        po = list(inspect.signature(getattr(old, fn)).parameters.values())
        pn = list(inspect.signature(getattr(new, fn)).parameters.values())
        assert [p.name for p in pn[:-1]] == [p.name for p in po], (fn, "old parameters must keep order and names")
        assert pn[-1].name == "groups" and pn[-1].default is None, (fn, "new parameter must be groups=None, last")
        for a, b in zip(po, pn[:-1]):
            if a.name == "rng":                  # a module-level Generator: a distinct object per module
                assert a.default is old.RNG and b.default is new.RNG
                assert type(a.default) is type(b.default)
            else:
                assert a.default == b.default or (a.default is b.default), (fn, a.name, "defaults must not change")


def test_live_file_is_the_one_arena_imports():
    paths.use_arena()
    import arena.siting as live
    assert Path(live.__file__).resolve() == (LIVE_ARENA / "siting.py").resolve()
    assert "groups" in inspect.signature(live.allocate).parameters


def test_no_other_arena_file_changed():
    """Only siting.py may differ from the backup; every other arena file is byte-identical."""
    def md5(p):
        return hashlib.md5(Path(p).read_bytes()).hexdigest()
    names = sorted(p.name for p in BACKUP_ARENA.glob("*.py"))
    assert "siting.py" in names and len(names) >= 10
    for n in names:
        same = md5(BACKUP_ARENA / n) == md5(LIVE_ARENA / n)
        if n == "siting.py":
            assert not same, "siting.py should differ (it holds the hook)"
        else:
            assert same, f"arena/{n} was modified"


def test_allocate_bit_identical_500plus_cases():
    old, new = _fresh()
    n_cases = 0
    for rule in RULES:
        for use_avail in (False, True):
            for use_caps in (False, True):
                for s in range(N_SEEDS_ALLOCATE):
                    seed = 1_000_003 * (RULES.index(rule) + 1) + 10_007 * use_avail + 101 * use_caps + s
                    g = np.random.default_rng(seed)
                    years, n_opt = int(g.integers(2, 15)), int(g.integers(1, 26))
                    vals = _values(g, years, n_opt)
                    y_idx = int(g.integers(0, years + 1))
                    mw = int(g.choice([1, 3, 5, 10, 17]))
                    av = _avail(g, n_opt) if use_avail else None
                    cl = _caps(g, n_opt) if use_caps else None
                    a_old = old.allocate(rule, vals, y_idx, mw, av, cl, np.random.default_rng(seed))
                    a_new = new.allocate(rule, vals, y_idx, mw, av, cl, np.random.default_rng(seed))
                    assert _same(a_old, a_new), (rule, seed, a_old, a_new)
                    # explicit groups=None and groups=[] must be the same call
                    a_n1 = new.allocate(rule, vals, y_idx, mw, av, cl, np.random.default_rng(seed), groups=None)
                    a_n2 = new.allocate(rule, vals, y_idx, mw, av, cl, np.random.default_rng(seed), groups=[])
                    assert _same(a_old, a_n1) and _same(a_old, a_n2), (rule, seed)
                    n_cases += 1
    assert n_cases >= 500, n_cases
    print(f"      allocate: {n_cases} cases identical", flush=True)


def test_portfolio_bit_identical_500plus_cases():
    old, new = _fresh()
    n_cases = 0
    for rule in RULES:
        for use_avail in (False, True):
            for use_caps in (False, True):
                for s in range(N_SEEDS_PORTFOLIO):
                    seed = 7_000_001 * (RULES.index(rule) + 1) + 20_011 * use_avail + 211 * use_caps + s
                    g = np.random.default_rng(seed)
                    years, n_opt = int(g.integers(4, 17)), int(g.integers(1, 22))
                    known = _values(g, years, n_opt)
                    real = known if g.random() < 0.5 else _values(g, years, n_opt)
                    y0 = int(g.integers(1, years - 1))
                    n_y = int(g.integers(1, years - y0 + 1))
                    mwy = int(g.choice([1, 2, 5, 10]))
                    av = _avail(g, n_opt, (years,)) if use_avail else None
                    cp = _caps(g, n_opt) if use_caps else None
                    p_old = old.portfolio(rule, known, real, y0, n_y, mwy, av, cp, np.random.default_rng(seed))
                    p_new = new.portfolio(rule, known, real, y0, n_y, mwy, av, cp, np.random.default_rng(seed))
                    p_n2 = new.portfolio(rule, known, real, y0, n_y, mwy, av, cp, np.random.default_rng(seed),
                                         groups=[])
                    for p in (p_new, p_n2):
                        assert set(p) == set(p_old)
                        for k in p_old:
                            assert _same(p_old[k], p[k]), (rule, seed, k)
                    n_cases += 1
    assert n_cases >= 500, n_cases
    print(f"      portfolio: {n_cases} cases identical", flush=True)


def test_default_module_rng_stays_in_step():
    """No rng argument: each module draws from its own module-level RNG. Calling both in lockstep must agree."""
    old, new = _fresh()
    g = np.random.default_rng(42)
    for i in range(60):
        years, n_opt = int(g.integers(3, 10)), int(g.integers(2, 12))
        vals = _values(g, years, n_opt)
        y_idx = int(g.integers(1, years + 1))
        a_old = old.allocate("A5ts", vals, y_idx, 10)
        a_new = new.allocate("A5ts", vals, y_idx, 10)
        assert _same(a_old, a_new), i
    p_old = old.portfolio("A5ts", vals, vals, 1, max(1, years - 2), 10)
    p_new = new.portfolio("A5ts", vals, vals, 1, max(1, years - 2), 10)
    assert _same(p_old["fleet"], p_new["fleet"])


def test_real_arena_call_shapes():
    """The exact call patterns of run_arena.py (positional rng, avail row, caps vector) are unchanged."""
    old, new = _fresh()
    g = np.random.default_rng(5)
    V = _values(g, 11, 14)
    avail = np.concatenate([np.ones((5, 14), bool), g.random((6, 14)) < 0.8], 0)
    caps = np.where(g.random(14) < 0.3, 30.0, np.inf)
    for a in RULES:
        for n in (1, 3, 5):
            po = old.portfolio(a, V, V, 5, n, 10, avail, caps, np.random.default_rng(100))
            pn = new.portfolio(a, V, V, 5, n, 10, avail, caps, np.random.default_rng(100))
            assert _same(po["fleet"], pn["fleet"]) and po["usd_per_mw_yr"] == pn["usd_per_mw_yr"]
        fo = old.allocate(a, V, 5, 10, avail[5], caps, np.random.default_rng(1))
        fn = new.allocate(a, V, 5, 10, avail[5], caps, np.random.default_rng(1))
        assert _same(fo, fn)
        po = old.portfolio(a, V, V, 1, 4, 10, rng=np.random.default_rng(11))
        pn = new.portfolio(a, V, V, 1, 4, 10, rng=np.random.default_rng(11))
        assert _same(po["fleet"], pn["fleet"])


def test_comparison_has_power_to_detect_a_change():
    """Sanity check on the test itself: a one-constant mutation of the backup source, per rule, must be caught."""
    src = (BACKUP_ARENA / "siting.py").read_text(encoding="utf-8")
    mutations = {
        "A1": ("return values[y_idx - 1]", "return values[y_idx - 1] + 0.001 * np.arange(values.shape[1])"),
        "A2": ("return h3.mean(0) + 0.5 * slope", "return h3.mean(0) + 0.4 * slope"),
        "A3": ("return h5.mean(0) - 0.5 * (h5.std(0) if len(h5) > 1 else 0)",
               "return h5.mean(0) - 0.6 * (h5.std(0) if len(h5) > 1 else 0)"),
        "A4": ("return h3.max(0)", "return h3.max(0) - 0.01 * h3.min(0)"),
        "A5": ("wts = 0.6 ** np.arange(k)[::-1]", "wts = 0.62 ** np.arange(k)[::-1]"),
        "A5ts": ("pick = np.argmax(np.where(room, rng.normal(mu, sd), -np.inf))",
                 "pick = np.argmax(np.where(room, rng.normal(mu, 1.1 * sd), -np.inf))"),
    }
    old, _ = _fresh()
    for rule, (a, b) in mutations.items():
        assert src.count(a) >= 1, (rule, a)
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("siting_mutant", loader=None))
        exec(compile(src.replace(a, b), "siting_mutant", "exec"), mod.__dict__)
        n_diff = 0
        for s in range(N_SEEDS_ALLOCATE):
            g = np.random.default_rng(900_000 + s)
            years, n_opt = int(g.integers(3, 15)), int(g.integers(2, 26))
            vals = _values(g, years, n_opt)
            y_idx = int(g.integers(1, years + 1))
            a0 = old.allocate(rule, vals, y_idx, 10, None, None, np.random.default_rng(s))
            a1 = mod.allocate(rule, vals, y_idx, 10, None, None, np.random.default_rng(s))
            n_diff += not _same(a0, a1)
        assert n_diff >= 1, f"the comparison would not notice a change to rule {rule}"


# ----------------------------------------------------------------------------------- part 2: group caps bind
def _rank_values(years=8, n_opt=8):
    """Option 0 best, then 1, 2, ...: every rule wants the low indices first."""
    v = np.zeros((years, n_opt))
    for k in range(n_opt):
        v[:, k] = 100 - 10 * k
    return v


def test_group_cap_binds_allocate_every_rule():
    _, new = _fresh()
    V = _rank_values()
    for rule in RULES:
        for cap in (0.0, 1.0, 4.0, 7.0, 9.0):
            a = new.allocate(rule, V, 5, 10, None, None, np.random.default_rng(3), [([0, 1, 2], cap)])
            assert a[[0, 1, 2]].sum() <= cap + 1e-9, (rule, cap, a)
            # the MW the group cannot take go elsewhere: nothing is lost while other options have room
            assert abs(a.sum() - 10) < 1e-9, (rule, cap, a)
            assert (a >= 0).all() and (a == np.round(a)).all()
        # an unbinding cap changes nothing
        free = new.allocate(rule, V, 5, 10, None, None, np.random.default_rng(3))
        big = new.allocate(rule, V, 5, 10, None, None, np.random.default_rng(3), [([0, 1, 2], 1e6)])
        assert _same(free, big), rule


def test_full_group_blocks_all_members():
    _, new = _fresh()
    V = _rank_values(n_opt=6)
    for rule in RULES:
        a = new.allocate(rule, V, 5, 10, None, np.array([np.inf] * 6), np.random.default_rng(1), [([0, 1, 2], 0.0)])
        assert a[[0, 1, 2]].sum() == 0, (rule, a)
        assert a[[3, 4, 5]].sum() == 10, (rule, a)
        # a group with 0.5 MW left is also full (the arena allocates whole MW)
        b = new.allocate(rule, V, 5, 10, None, None, np.random.default_rng(1), [([0, 1, 2], 0.5)])
        assert b[[0, 1, 2]].sum() == 0, (rule, b)


def test_group_blocks_even_the_top_option():
    """A1 puts everything in the best option; with the best option's group capped, the overflow walks down."""
    _, new = _fresh()
    V = _rank_values(n_opt=5)
    a = new.allocate("A1", V, 5, 10, None, None, np.random.default_rng(1), [([0], 3.0)])
    assert a[0] == 3 and a.sum() == 10
    assert a[1] == 7, a                      # the runner-up takes the overflow


def test_group_cap_is_cumulative_in_portfolio():
    """Counted across the fleet, like caps: once the group holds its cap it takes no more MW in later years."""
    _, new = _fresh()
    V = _rank_values(years=12, n_opt=6)
    for rule in RULES:
        p = new.portfolio(rule, V, V, 3, 6, 10, groups=[([0, 1], 7.0)], rng=np.random.default_rng(2))
        in_group = p["fleet"][:, [0, 1]].sum(1)
        assert (in_group <= 7.0 + 1e-9).all(), (rule, in_group)
        assert np.allclose(p["fleet"].sum(1), 10 * np.arange(1, 7)), (rule, p["fleet"].sum(1))
        if rule == "A1":                     # A1 goes all-in on option 0: 7 MW in year 1, then the group stays at 7
            assert in_group.tolist() == [7.0] * 6, in_group
        # a per-year reading would let the group reach 7 MW each year; the cumulative cap forbids it
        assert in_group[-1] <= 7.0


def test_overlapping_groups_both_apply():
    _, new = _fresh()
    V = _rank_values(years=10, n_opt=6)
    groups = [([0, 1], 3.0), ([1, 2], 2.0)]       # option 1 sits in both
    for rule in RULES:
        p = new.portfolio(rule, V, V, 2, 5, 10, groups=groups, rng=np.random.default_rng(4))
        f = p["fleet"]
        assert (f[:, [0, 1]].sum(1) <= 3.0 + 1e-9).all(), (rule, f)
        assert (f[:, [1, 2]].sum(1) <= 2.0 + 1e-9).all(), (rule, f)


def test_groups_combine_with_option_caps_and_avail():
    _, new = _fresh()
    V = _rank_values(years=10, n_opt=7)
    avail = np.ones((10, 7), bool)
    avail[:, 3] = False                           # option 3 never available
    caps = np.array([np.inf, 2.0, np.inf, np.inf, np.inf, np.inf, np.inf])
    groups = [([0, 1, 2], 5.0), ([4, 5], 4.0)]
    for rule in RULES:
        p = new.portfolio(rule, V, V, 2, 6, 10, avail, caps, np.random.default_rng(8), groups)
        f = p["fleet"]
        assert (f[:, 3] == 0).all(), rule
        assert (f[:, 1] <= 2.0 + 1e-9).all(), rule
        assert (f[:, [0, 1, 2]].sum(1) <= 5.0 + 1e-9).all(), rule
        assert (f[:, [4, 5]].sum(1) <= 4.0 + 1e-9).all(), rule
        assert np.allclose(f.sum(1), 10 * np.arange(1, 7)), (rule, f.sum(1))   # option 6 absorbs the rest


def test_everything_blocked_returns_partial_allocation():
    _, new = _fresh()
    V = _rank_values(n_opt=4)
    for rule in RULES:
        a = new.allocate(rule, V, 5, 10, None, None, np.random.default_rng(1), [([0, 1, 2, 3], 4.0)])
        assert a.sum() == 4, (rule, a)
        z = new.allocate(rule, V, 5, 10, None, None, np.random.default_rng(1), [([0, 1, 2, 3], 0.0)])
        assert z.sum() == 0
    p = new.portfolio("A5", V, V, 2, 5, 10, groups=[([0, 1, 2, 3], 12.0)], rng=np.random.default_rng(1))
    assert np.allclose(p["fleet"].sum(1), [10, 12, 12, 12, 12])


def test_profit_not_higher_when_the_best_options_are_capped():
    _, new = _fresh()
    V = _rank_values(years=12, n_opt=6)
    free = new.portfolio("A1", V, V, 3, 6, 10, rng=np.random.default_rng(1))
    capped = new.portfolio("A1", V, V, 3, 6, 10, groups=[([0, 1], 7.0)], rng=np.random.default_rng(1))
    assert capped["profit"].sum() < free["profit"].sum()
    assert capped["mw_years"] == free["mw_years"]


def test_groups_validation_and_mask_input():
    _, new = _fresh()
    V = _rank_values(n_opt=5)
    for bad in ([([0, 5], 3.0)], [([-1], 3.0)], [([0], float("nan"))]):
        try:
            new.allocate("A1", V, 5, 10, groups=bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad}")
    try:
        new.portfolio("A1", V, V, 2, 3, 10, groups=[([0], -1.0)])
    except ValueError:
        pass
    else:
        raise AssertionError("negative portfolio group cap must raise")
    mask = np.array([True, True, False, False, False])
    a = new.allocate("A1", V, 5, 10, groups=[(mask, 3.0)], rng=np.random.default_rng(1))
    b = new.allocate("A1", V, 5, 10, groups=[([0, 1], 3.0)], rng=np.random.default_rng(1))
    c = new.allocate("A1", V, 5, 10, groups=[([1, 0, 1], 3.0)], rng=np.random.default_rng(1))   # duplicates ignored
    assert _same(a, b) and _same(b, c)
    # an empty group never blocks anything
    d = new.allocate("A1", V, 5, 10, groups=[([], 0.0)], rng=np.random.default_rng(1))
    assert _same(d, new.allocate("A1", V, 5, 10, rng=np.random.default_rng(1)))


def test_group_semantics_match_a_hand_computed_case():
    """A3 aims 50/30/20% of 10 MW at the top three options and picks, each MW, the option furthest below its share
    (ties go to the higher index). Options 0 and 1 share a 6 MW cap. Hand trace (picks in order):
      MW1 -> 0, MW2 -> 0, MW3 -> 1 (tie 3-3), MW4 -> 0, MW5 -> 2 (tie 2-2-2), MW6 -> 1, MW7 -> 0.
    That is 4 + 2 = 6 MW in the group, which is now full. The ranking then excludes options 0 and 1, so the shares
    apply to options 2, 3, 4 (50/30/20): MW8 -> 2, MW9 -> 3 (tie 3-3), MW10 -> 2.  Result [4, 2, 3, 1, 0]."""
    _, new = _fresh()
    V = _rank_values(n_opt=5)
    a = new.allocate("A3", V, 5, 10, groups=[([0, 1], 6.0)], rng=np.random.default_rng(1))
    assert a.tolist() == [4.0, 2.0, 3.0, 1.0, 0.0], a
    # without the group the same rule gives the plain 50/30/20 split
    f = new.allocate("A3", V, 5, 10, rng=np.random.default_rng(1))
    assert f.tolist() == [5.0, 3.0, 2.0, 0.0, 0.0], f


if __name__ == "__main__":
    H.run_module(globals())
