#!/usr/bin/env bash
set -euo pipefail
task_project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_project_root"
export HF_HUB_CACHE="$task_project_root/.cache/huggingface/hub"
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
export TRITON_CACHE_DIR="$task_project_root/.cache/triton"
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
.venv/bin/python -X utf8 run.py prepare-dataset recordings --output datasets/combat-v005 --stride 3 --validation-run isaac-seed:2312680804 --validation-run isaac-seed:2171918557 --test-run isaac-seed:2215621234 --test-run isaac-seed:2587423712 --exclude-active-items --action-lag-frames 4 --without-control-context --feature-profile combat-build-terrain-weapons-v5
.venv/bin/python -X utf8 scripts/check_v005_dataset.py datasets/combat-v005
.venv/bin/python -X utf8 run.py train datasets/combat-v005 --output artifacts/combat-v005 --model Qwen/Qwen3.5-2B --revision 15852e8c16360a2fea060d615a32b45270f8a8fc --max-length 3072 --epochs 60 --patience 8 --min-delta 0.005 --feature-batch-size 16 --feature-pad-multiple 512 --cpu-threads 1 --move-transition-weight 3 --shoot-transition-weight 8
