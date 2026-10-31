"""``inferia-sim`` command line."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
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


def _cmd_run(args: argparse.Namespace) -> int:
    # Imported here so `validate` / `--help` stay fast.
    from simulator.engine import Simulation
    from simulator.report import command_line, summarize, write_outputs

    scenario, digest = load_scenario(args.scenario)
    started = time.perf_counter()
    result = Simulation(scenario, seed=args.seed, check_invariants=args.check_invariants).run()
    summary = summarize(result, scenario, config_hash=digest, command=command_line())
    out = Path(args.out)
    write_outputs(summary, result, out, write_requests=args.write_requests)
    t = summary["totals"]
    ttft = summary["latency"]["ttft_seconds"]["p95"]
    print(
        f"SIMULATED {scenario.name} seed={result.seed}: {t['requests']} requests "
        f"({t['completed']} completed, {t['failed']} failed, {t['shed']} shed, "
        f"{t['unfinished']} unfinished), preemptions={t['preempted']}, "
        f"TTFT p95={'n/a' if ttft is None else f'{ttft:.3f}s'}, "
        f"wall {time.perf_counter() - started:.1f}s -> {out / 'summary.json'}"
    )
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

    sp = sub.add_parser("run", help="run a scenario and write summary.json + manifest.json")
    sp.add_argument("scenario", help="scenario file, or the name of a bundled scenario")
    sp.add_argument("--seed", type=int, default=None, help="override run.seed")
    sp.add_argument("--out", required=True, help="output directory, e.g. bench/results/<date>-<run-id>")
    sp.add_argument("--write-requests", action="store_true", help="also write per-request requests.csv")
    sp.add_argument(
        "--check-invariants", action="store_true", help="assert KV-cache invariants every step (slow)"
    )
    sp.set_defaults(func=_cmd_run)
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
