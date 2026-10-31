"""``inferia-sim`` command line."""

from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from pydantic import ValidationError

from simulator import __version__
from simulator.config import bundled_scenarios, load_scenario


def _cmd_list(_args: argparse.Namespace) -> int:
    for name in bundled_scenarios():
        print(name)
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    scenario, digest = load_scenario(args.scenario)
    print(f"ok: {scenario.name} (sha256 {digest[:12]})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="inferia-sim",
        description="Discrete-event simulator of LLM inference serving. "
        "All outputs are SIMULATED, never GPU measurements.",
    )
    p.add_argument("--version", action="version", version=f"inferia-sim {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("list-scenarios", help="list the scenarios bundled with the package")
    sp.set_defaults(func=_cmd_list)

    sp = sub.add_parser("validate", help="validate a scenario file or bundled scenario name")
    sp.add_argument("scenario")
    sp.set_defaults(func=_cmd_validate)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError, ValidationError) as exc:
        # ValidationError subclasses ValueError; both are user input errors.
        print(f"inferia-sim: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
