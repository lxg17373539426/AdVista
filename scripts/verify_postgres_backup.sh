#!/usr/bin/env bash
set -euo pipefail

BACKUP="${1:-}"
if [[ -z "$BACKUP" || ! -f "$BACKUP" ]]; then
  printf '%s\n' "Usage: $0 /path/to/advista-*.dump" >&2
  exit 2
fi

/opt/miniconda3/bin/pg_restore --list "$BACKUP" >/dev/null
printf '%s\n' "PostgreSQL backup archive is readable: $BACKUP"
