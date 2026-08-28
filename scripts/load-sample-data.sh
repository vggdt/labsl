#!/usr/bin/env bash
# Creates a local Postgres role/database (matching .env.example) and loads
# the sample e-commerce dataset from db/seed.sql. Run this once against a
# Postgres server you control before `npm run dev`.
#
# Usage: ./scripts/load-sample-data.sh
# Env overrides: PGSUPERUSER (default: postgres), DB_USER, DB_PASS, DB_NAME

set -euo pipefail

if [ -z "${PGSUPERUSER:-}" ]; then
  if [ "$(uname)" = "Darwin" ]; then
    # Homebrew/Postgres.app initialize the cluster with the invoking macOS
    # user as the bootstrap superuser — there is no "postgres" OS account.
    PGSUPERUSER="$(id -un)"
  else
    # Debian/Ubuntu's postgresql package creates a dedicated "postgres" OS
    # user and only allows local-socket peer auth as that user.
    PGSUPERUSER="postgres"
  fi
fi
DB_USER="${DB_USER:-cube}"
DB_PASS="${DB_PASS:-cube_pw}"
DB_NAME="${DB_NAME:-semantic_layer}"

cd "$(dirname "$0")/.."

run_psql() {
  # -d postgres: without an explicit dbname, psql defaults to a database
  # named after the connecting role, which only exists by coincidence.
  # The "postgres" maintenance database always exists after initdb.
  if [ "$(id -un)" = "$PGSUPERUSER" ]; then
    psql -U "$PGSUPERUSER" -d postgres -v ON_ERROR_STOP=1 "$@"
  elif command -v sudo >/dev/null 2>&1 && id "$PGSUPERUSER" >/dev/null 2>&1; then
    sudo -u "$PGSUPERUSER" psql -d postgres -v ON_ERROR_STOP=1 "$@"
  else
    psql -U "$PGSUPERUSER" -d postgres -v ON_ERROR_STOP=1 "$@"
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
