"""Minimal test runner for agent 3a's tests: the research venv has no pytest.

Each test module defines plain `test_*` functions (pytest would collect them too) and ends with

    if __name__ == "__main__":
        run_module(globals())

Run one module:   python tests/test_arena_compat.py [name-substring ...]
Exit status is 0 only if every selected test passed.
"""
import sys
import time
import traceback
from pathlib import Path

GRID_DIR = Path(__file__).resolve().parents[1]
if str(GRID_DIR) not in sys.path:
    sys.path.insert(0, str(GRID_DIR))


def run_module(ns, argv=None):
    argv = sys.argv[1:] if argv is None else argv
    tests = [(k, v) for k, v in ns.items() if k.startswith("test_") and callable(v)]
    if argv:
        tests = [(k, v) for k, v in tests if any(a in k for a in argv)]
    failed = []
    t_all = time.time()
    for name, fn in tests:
        t0 = time.time()
        try:
            fn()
            print(f"PASS  {name}  ({time.time() - t0:.1f}s)", flush=True)
        except Exception:
            failed.append(name)
            print(f"FAIL  {name}  ({time.time() - t0:.1f}s)", flush=True)
            traceback.print_exc()
    print(f"{len(tests) - len(failed)}/{len(tests)} passed in {time.time() - t_all:.1f}s", flush=True)
    if failed:
        print("FAILED:", ", ".join(failed))
        sys.exit(1)
