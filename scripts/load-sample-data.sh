#!/usr/bin/env bash
# Creates a local Postgres role/database (matching .env.example) and loads
# the sample e-commerce dataset from db/seed.sql. Run this once against a
# Postgres server you control before `npm run dev`.
#
# Usage: ./scripts/load-sample-data.sh
# Env overrides: PGSUPERUSER (default: postgres), DB_USER, DB_PASS, DB_NAME

set -euo pipefail

PGSUPERUSER="${PGSUPERUSER:-postgres}"
DB_USER="${DB_USER:-cube}"
DB_PASS="${DB_PASS:-cube_pw}"
DB_NAME="${DB_NAME:-semantic_layer}"

cd "$(dirname "$0")/.."

run_psql() {
  # Debian/Ubuntu Postgres only allows local-socket peer auth as the
  # `postgres` role when run *as* the `postgres` OS user, so shell out via
  # sudo unless we already are that user (e.g. inside a minimal container).
  if [ "$(id -un)" = "$PGSUPERUSER" ]; then
    psql -U "$PGSUPERUSER" -v ON_ERROR_STOP=1 "$@"
  else
    sudo -u "$PGSUPERUSER" psql -v ON_ERROR_STOP=1 "$@"
  fi
}

if ! run_psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1; then
  run_psql -c "CREATE ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASS}' CREATEDB;"
fi

if ! run_psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
  run_psql -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};"
fi

PGPASSWORD="$DB_PASS" psql -h localhost -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -f db/seed.sql

echo "Loaded sample data into ${DB_NAME} (user: ${DB_USER})."
