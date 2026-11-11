#!/bin/sh
# Runs once on a fresh volume, after 001-runtime-roles.sql (which creates the logins with the dev
# default password). Gives each runtime login its own password so a compromised API process
# cannot log in as settlement (R22). Passwords come from the postgres container's environment.
set -eu
: "${S1_API_DB_PASSWORD:?run scripts/s1_init_secrets.py first}"
: "${S1_SETTLE_DB_PASSWORD:?run scripts/s1_init_secrets.py first}"
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -v api_pw="$S1_API_DB_PASSWORD" -v settle_pw="$S1_SETTLE_DB_PASSWORD" <<'SQL'
ALTER ROLE aegis_api PASSWORD :'api_pw';
ALTER ROLE aegis_settle PASSWORD :'settle_pw';
SQL
