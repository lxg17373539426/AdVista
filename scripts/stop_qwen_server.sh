#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="${1:-$ROOT/outputs/services/default/server.pid}"
case "$PID_FILE" in
  "$ROOT/outputs/services/"*/server.pid) ;;
  *) printf 'Refusing PID file outside project outputs/services: %s\n' "$PID_FILE" >&2; exit 2 ;;
esac
if [[ ! -f "$PID_FILE" ]]; then printf 'PID file does not exist: %s\n' "$PID_FILE"; exit 0; fi
PID="$(<"$PID_FILE")"
if kill -0 "$PID" 2>/dev/null; then kill "$PID"; fi
rm -f "$PID_FILE"
printf 'Stopped PID=%s\n' "$PID"
