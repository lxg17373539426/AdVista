#!/usr/bin/env bash
set -euo pipefail

export QWEN_EXPERIMENT="production_bf16_c16_graph_prefix1"
export QWEN_MAX_NUM_SEQS="2"
export QWEN_MAX_NUM_BATCHED_TOKENS="8192"
export QWEN_MAX_MODEL_LEN="262144"
export QWEN_ENABLE_PREFIX_CACHING="1"
export QWEN_ENFORCE_EAGER="0"
export QWEN_DTYPE="bfloat16"
export QWEN_MAX_IMAGES_PER_PROMPT="8"
unset QWEN_QUANTIZATION

exec bash "$(dirname "$0")/start_qwen_server.sh"
