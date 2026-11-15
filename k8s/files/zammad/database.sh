#!/bin/sh
set -eu
export PGPASSWORD="$POSTGRES_ADMIN_PASSWORD"
until psql --host="$POSTGRESQL_HOST" --username="$POSTGRES_ADMIN_USER" --dbname=postgres --tuples-only \
    --command='SELECT 1' >/dev/null 2>&1; do
  echo "Waiting for PostgreSQL..."
  sleep 3
done
psql --host="$POSTGRESQL_HOST" --username="$POSTGRES_ADMIN_USER" --dbname=postgres --set=ON_ERROR_STOP=1 \
  --set=db_user=zammad --set=db_password="$POSTGRESQL_PASS" <<'SQL'
-- Keep the password out of the database audit log.
SET log_statement = 'none';
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION', :'db_user')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'db_user')
\gexec
-- The shared server allows 100 connections; Zammad's three processes keep to 12 each
-- (POSTGRESQL_OPTIONS) and the role can never take more than 40.
SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION CONNECTION LIMIT 40', :'db_user', :'db_password')
\gexec
SELECT format('CREATE DATABASE %I OWNER %I', 'zammad', :'db_user')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'zammad')
\gexec
SQL
