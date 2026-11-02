#!/usr/bin/env bash
# Rehearse and run a short, reproducible vLLM calibration lease.
#
# Default behaviour is a CPU-safe dry run.  Pass --execute only on a GPU host
# after vLLM has been started and its OpenAI-compatible endpoint is healthy.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bench/lease/run_lease.sh [--dry-run|--execute] --base-url URL [options]

Options:
  --model ID          Model name reported to the endpoint (default: meta-llama/Llama-3.1-8B-Instruct)
  --out DIR           Result directory (default: bench/results/YYYY-MM-DD-lease)
  --bench-command CMD Override the benchmark command. It receives $BASE_URL and $MODEL.

The default command uses `vllm bench serve` for the calibration matrix. Results
are MEASURED only when --execute completes on a GPU host; the simulator is never
labelled measured by this script.
EOF
}

mode=dry-run
base_url=
model=meta-llama/Llama-3.1-8B-Instruct
out="bench/results/$(date -u +%F)-lease"
bench_command=

while (($#)); do
  case "$1" in
    --dry-run) mode=dry-run ;;
    --execute) mode=execute ;;
    --base-url) base_url=${2:?missing value}; shift ;;
    --model) model=${2:?missing value}; shift ;;
    --out) out=${2:?missing value}; shift ;;
    --bench-command) bench_command=${2:?missing value}; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

[[ -n "$base_url" ]] || { echo "--base-url is required" >&2; exit 2; }
[[ "$out" == bench/results/* ]] || { echo "--out must be below bench/results/" >&2; exit 2; }

default_command='vllm bench serve --backend openai --base-url "$BASE_URL" --model "$MODEL" --dataset-name random --num-prompts 200 --request-rate 8 --random-input-len 1024 --random-output-len 128 --save-result'
command=${bench_command:-$default_command}

printf 'Lease calibration: %s\n' "$mode"
printf 'Endpoint: %s\nModel: %s\nOutput: %s\n' "$base_url" "$model" "$out"
printf 'Benchmark command: %s\n' "$command"

if [[ "$mode" == dry-run ]]; then
  echo 'Dry run successful. No GPU check, server request, or output was made.'
  exit 0
fi

command -v nvidia-smi >/dev/null || { echo 'nvidia-smi is required for --execute' >&2; exit 1; }
command -v vllm >/dev/null || { echo 'vllm CLI is required for --execute' >&2; exit 1; }
curl --fail --silent --show-error "${base_url%/}/health" >/dev/null
mkdir -p "$out"

{
  echo "label=MEASURED"
  echo "created_at=$(date -u +%FT%TZ)"
  echo "base_url=$base_url"
  echo "model=$model"
  echo "git_sha=$(git rev-parse HEAD)"
  echo "vllm_version=$(vllm --version)"
  nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
} >"$out/manifest.env"

BASE_URL="$base_url" MODEL="$model" eval "$command" | tee "$out/benchmark.log"
printf 'MEASURED lease result written to %s\n' "$out"
