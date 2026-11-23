#!/usr/bin/env bash
set -euo pipefail
task_project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_project_root"
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv --system-site-packages .venv
fi
source .venv/bin/activate
if [[ -d wheelhouse ]]; then
  python -m pip install --no-user --no-index --find-links wheelhouse 'setuptools>=68' wheel
  python -m pip install --no-user --no-index --find-links wheelhouse --no-build-isolation -e '.[train]'
else
  python -m pip install --no-user -e '.[train]'
fi
mkdir -p .local
python - <<'PY'
import importlib.metadata, json
from pathlib import Path
import torch
assert torch.cuda.is_available(), 'CUDA is unavailable'
device=torch.cuda.get_device_properties(0)
assert 'L40S' in device.name, f'Expected L40S, found {device.name}'
assert torch.cuda.is_bf16_supported(), 'BF16 is unavailable'
probe=torch.arange(16,device='cuda',dtype=torch.float32).sum().item()
assert probe==120
report={'torch':torch.__version__,'cuda':torch.version.cuda,'gpu':device.name,
        'memory_bytes':device.total_memory,'transformers':importlib.metadata.version('transformers'),
        'safetensors':importlib.metadata.version('safetensors'),'cuda_smoke_test':'passed'}
Path('.local/l40s-environment.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report,indent=2))
PY
