#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
if [[ -z "${ADVISTA_DATABASE_URL:-}" ]]; then
  printf '%s\n' "ADVISTA_DATABASE_URL is not configured" >&2
  exit 1
fi

DATABASE_URL="${ADVISTA_DATABASE_URL/postgresql+psycopg/postgresql}"
PG_DUMP="${PG_DUMP_BIN:-$(command -v pg_dump || true)}"
if [[ -z "$PG_DUMP" && -x /opt/miniconda3/bin/pg_dump ]]; then
  PG_DUMP=/opt/miniconda3/bin/pg_dump
fi
if [[ -z "$PG_DUMP" || ! -x "$PG_DUMP" ]]; then
  printf '%s\n' "pg_dump not found; install PostgreSQL client tools or set PG_DUMP_BIN" >&2
  exit 2
fi
BACKUP_DIR="${ROOT}/outputs/backups"
mkdir -p "$BACKUP_DIR"
TARGET="$(mktemp "${BACKUP_DIR}/advista-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX.dump")"
trap 'rm -f -- "$TARGET"' EXIT

# libpq accepts a connection URI through PGDATABASE. Keep credentials out of
# command arguments, which other local users can inspect with process tools.
PGDATABASE="$DATABASE_URL" "$PG_DUMP" \
  --format=custom \
  --no-owner \
  --file="$TARGET"

trap - EXIT
printf '%s\n' "$TARGET"
