#!/usr/bin/env bash
set -euo pipefail
cd /root/autodl-tmp/COMP4431
export HF_HOME=/root/autodl-tmp/hf-cache
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
export PYTHONUNBUFFERED=1 OMP_NUM_THREADS=8 MKL_NUM_THREADS=8
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
while [ ! -f runtime/download.ok ]; do
 if [ -f runtime/download.exit ] && [ "$(cat runtime/download.exit)" != 0 ]; then echo 'MODEL_DOWNLOAD_FAILED'; exit 1; fi
 sleep 5
done
python -m pip check
python runtime/run_training.py --model models/Qwen3.5-9B-Base --output runs/shared-economics --micro-batch 4 --grad-accum 4
