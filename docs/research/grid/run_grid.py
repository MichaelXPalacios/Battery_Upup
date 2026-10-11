"""Command line for the grid-siting build.

Each phase owner registers its subcommands in its own module, gridsite/cli_<phase>.py, as a dict
COMMANDS = {"name": function}. This file never needs editing, so parallel agents don't collide.

Run from docs/research/grid with the research venv:
  C:/Users/Javier/Repo/BaseP/research-data/.venv/Scripts/python.exe run_grid.py <command> [args...]
"""
import argparse
import importlib
import pathlib


def load_commands():
    cmds = {}
    for f in sorted((pathlib.Path(__file__).parent / "gridsite").glob("cli_*.py")):
        mod = importlib.import_module(f"gridsite.{f.stem}")
        cmds.update(getattr(mod, "COMMANDS", {}))
    return cmds


def main():
    cmds = load_commands()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=sorted(cmds) or ["none"])
    ap.add_argument("args", nargs="*")
    a = ap.parse_args()
    cmds[a.command](*a.args)


if __name__ == "__main__":
    main()
