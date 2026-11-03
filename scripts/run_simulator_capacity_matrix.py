#!/usr/bin/env python3
"""Run reproducible, CPU-backed *simulation* capacity experiments.

This executes the discrete-event model on the CPU; it never measures a GPU.
Every written artifact embeds the simulator's ``SIMULATED`` manifest and the
scenario's calibration label, which is currently ``uncalibrated``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from simulator.config import load_scenario
from simulator.engine import Simulation
from simulator.report import summarize, write_outputs


# Keep this deliberately small enough for routine CI/developer use. Longer
# planning sweeps should add an explicitly versioned matrix rather than alter
# these baseline points in place.
MATRIX = ("tiny", "8xH100", "64xH100", "hetero-h100-a100-l40s", "256xH100")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="artifact root")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--scenarios", nargs="*", default=MATRIX, choices=MATRIX)
    args = parser.parse_args()

    for name in args.scenarios:
        scenario, digest = load_scenario(name)
        result = Simulation(scenario, seed=args.seed, check_invariants=True).run()
        summary = summarize(
            result, scenario, digest,
            f"CPU-backed simulated capacity matrix scenario={name} seed={args.seed}",
        )
        summary["manifest"]["notes"] = (
            "SIMULATED / uncalibrated CPU-host execution. This is not a GPU benchmark "
            "and must not be used as a hardware capacity commitment."
        )
        write_outputs(summary, result, args.out / name, write_requests=True, write_plots=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
