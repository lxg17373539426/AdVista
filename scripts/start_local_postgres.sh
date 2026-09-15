#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
DATA_DIR="$ROOT/.postgresql/data"
LOG_FILE="$ROOT/.postgresql/server.log"

if runuser -u postgres -- /opt/miniconda3/bin/pg_ctl -D "$DATA_DIR" status >/dev/null 2>&1; then
  printf '%s\n' "PostgreSQL is already running on 127.0.0.1:55432"
  exit 0
fi

runuser -u postgres -- /opt/miniconda3/bin/pg_ctl \
  -D "$DATA_DIR" \
  -l "$LOG_FILE" \
  -o "-h 127.0.0.1 -p 55432" \
  start

printf '%s\n' "PostgreSQL started on 127.0.0.1:55432"
