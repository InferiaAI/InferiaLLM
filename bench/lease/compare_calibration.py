#!/usr/bin/env python3
"""Compare a real-engine lease summary with a SIMULATED replay summary."""

import argparse
import json
from pathlib import Path


METRICS = {
    "ttft_p95_s": ("latency", "ttft_seconds", "p95"),
    "itl_p95_s": ("latency", "itl_seconds", "p95"),
    "output_tokens_per_second": ("throughput", "output_tokens_per_second"),
}


def get(data: dict, path: tuple[str, ...]) -> float:
    value = data
    for key in path:
        value = value[key]
    if not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"metric {'.'.join(path)} must be a positive number")
    return float(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute simulator calibration error")
    parser.add_argument("--measured", type=Path, required=True)
    parser.add_argument("--simulated", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    measured = json.loads(args.measured.read_text())
    simulated = json.loads(args.simulated.read_text())
    if measured.get("manifest", {}).get("label") != "MEASURED":
        raise ValueError("--measured manifest.label must be MEASURED")
    if simulated.get("manifest", {}).get("label") != "SIMULATED":
        raise ValueError("--simulated manifest.label must be SIMULATED")
    rows = []
    for name, path in METRICS.items():
        real, sim = get(measured, path), get(simulated, path)
        rows.append({"metric": name, "measured": real, "simulated": sim, "ape_percent": abs(sim - real) / real * 100})
    result = {
        "measured_label": "MEASURED", "simulated_label": "SIMULATED", "metrics": rows,
        "mape_percent": sum(row["ape_percent"] for row in rows) / len(rows), "target_mape_percent": 15.0,
    }
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
