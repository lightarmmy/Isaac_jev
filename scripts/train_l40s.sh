#!/usr/bin/env bash
set -euo pipefail
task_project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_project_root"
source .venv/bin/activate
export HF_HOME="$task_project_root/.cache/huggingface"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
task_dataset="${1:-datasets/combat-v002}"
task_output="${2:-artifacts/combat-v002}"
python -X utf8 run.py train "$task_dataset" --output "$task_output" --model Qwen/Qwen3.5-2B --max-length 3072 --epochs 10
