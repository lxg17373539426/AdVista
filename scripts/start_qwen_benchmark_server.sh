#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL_PATH="${QWEN_MODEL_PATH:-$ROOT/models/Qwen3.5-9B-GSPO-Step300-Merged}"
VIDEO_DIR="${QWEN_VIDEO_DIR:-$ROOT/data/videos}"
PYTHON="${QWEN_PYTHON:-$ROOT/.venv-vllm/bin/python}"
PORT="${QWEN_PORT:-8000}"
GPU="${QWEN_GPU:-0}"
GPU_MEM="${QWEN_GPU_MEMORY_UTILIZATION:-0.90}"
MAX_MODEL_LEN="${QWEN_MAX_MODEL_LEN:-11264}"
MAX_NUM_SEQS="${QWEN_MAX_NUM_SEQS:-8}"
MAX_NUM_BATCHED_TOKENS="${QWEN_MAX_NUM_BATCHED_TOKENS:-8192}"
MAX_IMAGES_PER_PROMPT="${QWEN_MAX_IMAGES_PER_PROMPT:-0}"
EXPERIMENT="${QWEN_EXPERIMENT:-bf16_c8_eager0_prefix0}"
DTYPE="${QWEN_DTYPE:-bfloat16}"
QUANTIZATION="${QWEN_QUANTIZATION:-}"
PREFIX_FLAG="--no-enable-prefix-caching"
EAGER_FLAG=()
QUANTIZATION_FLAG=()

if [[ ! -x "$PYTHON" ]]; then
  printf 'vLLM Python not found: %s\nSet QWEN_PYTHON to the correct interpreter.\n' "$PYTHON" >&2
  exit 2
fi
if [[ ! -d "$MODEL_PATH" ]]; then
  printf 'Model directory not found: %s\nSet QWEN_MODEL_PATH to the AdInsight-RL checkpoint.\n' "$MODEL_PATH" >&2
  exit 2
fi
if [[ ! -d "$VIDEO_DIR" ]]; then
  printf 'Video directory not found: %s\nSet QWEN_VIDEO_DIR to a directory containing allowed media.\n' "$VIDEO_DIR" >&2
  exit 2
fi

if [[ "${QWEN_ENABLE_PREFIX_CACHING:-0}" == "1" ]]; then
  PREFIX_FLAG="--enable-prefix-caching"
fi
if [[ "${QWEN_ENFORCE_EAGER:-0}" == "1" ]]; then
  EAGER_FLAG=(--enforce-eager)
fi
if [[ -n "$QUANTIZATION" ]]; then
  QUANTIZATION_FLAG=(--quantization "$QUANTIZATION")
fi

RUN_DIR="$ROOT/outputs/benchmarks/mac3108/services/$EXPERIMENT"
mkdir -p "$RUN_DIR"
PID_FILE="$RUN_DIR/server.pid"
LOG_FILE="$RUN_DIR/server.log"
COMMAND_FILE="$RUN_DIR/command.txt"

if [[ -f "$PID_FILE" ]] && kill -0 "$(<"$PID_FILE")" 2>/dev/null; then
  printf 'Server already running with PID %s\n' "$(<"$PID_FILE")"
  exit 0
fi

CMD=(
  "$PYTHON" -m vllm.entrypoints.openai.api_server
  --model "$MODEL_PATH"
  --served-model-name AdInsight-RL
  --host 127.0.0.1
  --port "$PORT"
  --tensor-parallel-size 1
  --dtype "$DTYPE"
  --gpu-memory-utilization "$GPU_MEM"
  --max-model-len "$MAX_MODEL_LEN"
  --limit-mm-per-prompt "{\"video\":1,\"image\":$MAX_IMAGES_PER_PROMPT}"
  --allowed-local-media-path "$VIDEO_DIR"
  --mm-processor-cache-gb 0
  "$PREFIX_FLAG"
  --max-num-seqs "$MAX_NUM_SEQS"
  --max-num-batched-tokens "$MAX_NUM_BATCHED_TOKENS"
  --enable-chunked-prefill
  --reasoning-parser qwen3
  --trust-remote-code
  "${EAGER_FLAG[@]}"
  "${QUANTIZATION_FLAG[@]}"
)

printf 'CUDA_VISIBLE_DEVICES=%q ' "$GPU" > "$COMMAND_FILE"
printf '%q ' "${CMD[@]}" >> "$COMMAND_FILE"
printf '\n' >> "$COMMAND_FILE"
CUDA_VISIBLE_DEVICES="$GPU" nohup "${CMD[@]}" > "$LOG_FILE" 2>&1 &
printf '%s\n' "$!" > "$PID_FILE"
printf 'Started benchmark server PID=%s experiment=%s log=%s\n' "$!" "$EXPERIMENT" "$LOG_FILE"
