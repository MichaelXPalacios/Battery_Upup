"""See DESIGN.md for the owner and contract of this subpackage.

D1b: `lp.solve` implements the `contracts.Optimizer` Protocol (perfect foresight, forecast and fixed-action valuation,
one code path); `rolling.run_shadow` replays it as a receding-horizon shadow optimizer; `backtest.Backtest` is Gate 3's
`backtest_fn(spec, start_utc, end_utc) -> revenue_usd` over `run_shadow(step_intervals=1)`; `dp_check` ties the LP to the
research arena DP. Heavy imports (cvxpy) happen on the first solve, not on import."""
from .lp import solve  # noqa: F401
