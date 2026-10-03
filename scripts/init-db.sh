#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres -v learner_password="$LEARNER_PASSWORD" -v recorder_password="$RECORDER_PASSWORD" <<'SQL'
CREATE ROLE learner LOGIN PASSWORD :'learner_password';
CREATE ROLE recorder LOGIN PASSWORD :'recorder_password';
CREATE DATABASE training;
CREATE DATABASE records OWNER recorder;
REVOKE CONNECT, TEMPORARY ON DATABASE records FROM PUBLIC;
GRANT CONNECT ON DATABASE records TO recorder;
REVOKE CONNECT, TEMPORARY ON DATABASE training FROM PUBLIC;
GRANT CONNECT ON DATABASE training TO learner;
ALTER ROLE learner SET default_transaction_read_only = on;
ALTER ROLE learner SET statement_timeout = '5s';
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname training <<'SQL'
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;
SQL
