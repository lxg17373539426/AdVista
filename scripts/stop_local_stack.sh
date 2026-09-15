#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
PID_FILE="$ROOT/outputs/web-test.pid"

if [[ -f "$PID_FILE" ]]; then
  PID="$(<"$PID_FILE")"
  if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
  fi
  rm -f "$PID_FILE"
fi

QWEN_EXPERIMENT="${QWEN_EXPERIMENT:-local_test}" bash "$ROOT/scripts/stop_qwen_server.sh"
bash "$ROOT/scripts/stop_local_postgres.sh"
printf '%s\n' "AdVista local stack stopped"
