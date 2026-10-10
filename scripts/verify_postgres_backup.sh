#!/usr/bin/env bash
set -euo pipefail

BACKUP="${1:-}"
if [[ -z "$BACKUP" || ! -f "$BACKUP" ]]; then
  printf '%s\n' "Usage: $0 /path/to/advista-*.dump" >&2
  exit 2
fi

PG_RESTORE="${PG_RESTORE_BIN:-$(command -v pg_restore || true)}"
if [[ -z "$PG_RESTORE" && -x /opt/miniconda3/bin/pg_restore ]]; then
  PG_RESTORE=/opt/miniconda3/bin/pg_restore
fi
if [[ -z "$PG_RESTORE" || ! -x "$PG_RESTORE" ]]; then
  printf '%s\n' "pg_restore not found; install PostgreSQL client tools or set PG_RESTORE_BIN" >&2
  exit 2
fi
"$PG_RESTORE" --list "$BACKUP" >/dev/null
printf '%s\n' "PostgreSQL backup archive is readable: $BACKUP"
