# Benchmarks and GPU lease rehearsal

All outputs must use one evidence label:

- **SIMULATED**: `inferia-sim` output, including all CPU-only runs.
- **MEASURED**: a completed real-engine run; the environment manifest is kept beside it.
- **EXTRAPOLATED**: a documented calculation from one of the above.

## CPU rehearsal

This validates the lease command shape without a GPU, vLLM installation, network call, or filesystem output:

```bash
bash bench/lease/run_lease.sh --dry-run --base-url http://127.0.0.1:8000
```

Simulator scenarios remain usable without a GPU:

```bash
inferia-sim validate 8xH100
inferia-sim run 8xH100 --seed 1 --out bench/results/sim-8xh100-seed1 --write-requests
```

The latter result is **SIMULATED**, even though the scenario uses an H100
catalog entry.

## GPU calibration lease

1. Start vLLM with the intended model and record its flags separately.
2. Confirm its OpenAI-compatible `/health` endpoint.
3. Run the harness with `--execute`; it refuses to run without `nvidia-smi`,
   vLLM, or a healthy endpoint.

```bash
bash bench/lease/run_lease.sh --execute \
  --base-url http://127.0.0.1:8000 \
  --model meta-llama/Llama-3.1-8B-Instruct \
  --out bench/results/2026-11-02-h100-8b
```

`manifest.env` contains the result label, UTC timestamp, git SHA, vLLM
version, GPU model, driver and HBM. `benchmark.log` contains the tool output.
Use the same workload matrix in the simulator calibration report; do not turn a
dry run or a simulator result into a hardware claim.
