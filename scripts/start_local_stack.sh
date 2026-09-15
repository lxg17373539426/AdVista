#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  printf '%s\n' "Missing $ROOT/.env" >&2
  exit 2
fi

set -a
source .env
set +a

bash scripts/start_local_postgres.sh

if ! curl -fsS --max-time 2 http://127.0.0.1:8000/v1/models >/dev/null 2>&1; then
  QWEN_MODEL_PATH="${QWEN_MODEL_PATH:-/data/tec-chi/MARS2/MAC/model/Qwen3.5-9B-GSPO-Step300-Merged}" \
  QWEN_VIDEO_DIR="${QWEN_VIDEO_DIR:-/data/tec-chi/MARS2/MAC/dataset/mars2_videos}" \
  QWEN_PYTHON="${QWEN_PYTHON:-/opt/miniconda3/envs/vllm/bin/python}" \
  QWEN_GPU="${QWEN_GPU:-0}" \
  QWEN_EXPERIMENT="${QWEN_EXPERIMENT:-local_test}" \
  bash scripts/start_qwen_server.sh
fi

for _ in $(seq 1 60); do
  if curl -fsS --max-time 2 http://127.0.0.1:8000/v1/models >/dev/null 2>&1; then
    break
  fi
  sleep 5
done
curl -fsS --max-time 2 http://127.0.0.1:8000/v1/models >/dev/null

if ! curl -fsS --max-time 2 http://127.0.0.1:8080/api/health >/dev/null 2>&1; then
  setsid /opt/miniconda3/bin/advista-agent serve --config configs/local.yaml \
    > outputs/web-test.log 2>&1 < /dev/null &
  printf '%s\n' "$!" > outputs/web-test.pid
fi

for _ in $(seq 1 20); do
  if curl -fsS --max-time 2 http://127.0.0.1:8080/api/health >/dev/null 2>&1; then
    printf '%s\n' "AdVista local stack is ready: http://127.0.0.1:8080"
    exit 0
  fi
  sleep 1
done

printf '%s\n' "AdVista Web failed to become ready; inspect outputs/web-test.log" >&2
exit 1
