#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
if [[ -z "${ADVISTA_DATABASE_URL:-}" ]]; then
  printf '%s\n' "ADVISTA_DATABASE_URL is not configured" >&2
  exit 1
fi

DATABASE_URL="${ADVISTA_DATABASE_URL/postgresql+psycopg/postgresql}"
BACKUP_DIR="${ROOT}/outputs/backups"
mkdir -p "$BACKUP_DIR"
TARGET="${BACKUP_DIR}/advista-$(date -u +%Y%m%dT%H%M%SZ).dump"

/opt/miniconda3/bin/pg_dump \
  --format=custom \
  --no-owner \
  --file="$TARGET" \
  "$DATABASE_URL"

printf '%s\n' "$TARGET"
