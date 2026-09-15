#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
DATA_DIR="$ROOT/.postgresql/data"

if runuser -u postgres -- /opt/miniconda3/bin/pg_ctl -D "$DATA_DIR" status >/dev/null 2>&1; then
  runuser -u postgres -- /opt/miniconda3/bin/pg_ctl -D "$DATA_DIR" stop -m fast
else
  printf '%s\n' "PostgreSQL is not running"
fi
