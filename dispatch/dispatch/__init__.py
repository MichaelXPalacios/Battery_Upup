"""ERCOT battery dispatch platform: data, forecasts, LP optimizer, attribution, guarded paper dispatch.

See ../DESIGN.md. Models only forecast; deterministic code (the LP and the guard) decides every setpoint.
"""
__version__ = "0.1.0"
