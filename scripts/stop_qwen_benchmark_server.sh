#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$PROJECT_ROOT/outputs/benchmarks/mac3108/services"
PID_FILE="${1:-}"
if [[ -z "$PID_FILE" ]]; then
  printf 'Usage: %s /absolute/path/to/server.pid\n' "$0" >&2
  exit 2
fi
case "$PID_FILE" in
  "$ROOT"/*/server.pid) ;;
  *) printf 'Refusing PID file outside %s\n' "$ROOT" >&2; exit 2 ;;
esac
if [[ ! -f "$PID_FILE" ]]; then
  printf 'PID file does not exist: %s\n' "$PID_FILE"
  exit 0
fi
PID="$(<"$PID_FILE")"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  for _ in $(seq 1 15); do
    if ! kill -0 "$PID" 2>/dev/null; then break; fi
    sleep 1
  done
  if kill -0 "$PID" 2>/dev/null; then
    kill -9 "$PID"
  fi
fi
rm -f "$PID_FILE"
printf 'Stopped PID=%s\n' "$PID"
