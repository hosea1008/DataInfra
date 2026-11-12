#!/usr/bin/env bash
# make_mock_db.sh
# Idempotent provisioning of the local mock QA databases for the visualize app.
#   - ensures cluster 15/main is running
#   - sets postgres password to 'postgres'
#   - (re)creates role reader (LOGIN, NOSUPERUSER, PASSWORD 'postgres')
#   - DROP+CREATE visualize_mock_prod (seeded) and visualize_mock_test (junk only)
#   - grants SELECT on all public tables in prod to reader
# Safe to re-run: every step is DROP/CREATE or CREATE ... IF NOT EXISTS style.
set -euo pipefail

cd /  # postgres OS user cannot traverse /root; run psql from a neutral cwd

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEED_FILE="$SCRIPT_DIR/seed_mock.sql"

PSQL=(runuser -u postgres -- psql -X -q -v ON_ERROR_STOP=1)

# --- 0) cluster up -----------------------------------------------------------
if ! pg_lsclusters -h | awk '$1 == 15 && $2 == "main" && $4 == "online" { found=1 } END { exit !found }'; then
    echo "[make_mock_db] starting cluster 15/main"
    pg_ctlcluster 15 main start
fi
pg_lsclusters

# --- 1) postgres password ----------------------------------------------------
echo "[make_mock_db] setting postgres password"
"${PSQL[@]}" -d postgres -c "ALTER ROLE postgres PASSWORD 'postgres';"

# --- 2) drop databases, recreate role, recreate databases --------------------
echo "[make_mock_db] recreating role 'reader' and databases"
"${PSQL[@]}" -d postgres <<'SQL'
DROP DATABASE IF EXISTS visualize_mock_prod WITH (FORCE);
DROP DATABASE IF EXISTS visualize_mock_test WITH (FORCE);
DROP ROLE IF EXISTS reader;
CREATE ROLE reader LOGIN NOSUPERUSER PASSWORD 'postgres';
CREATE DATABASE visualize_mock_prod;
CREATE DATABASE visualize_mock_test;
SQL

# --- 3) seed prod ------------------------------------------------------------
echo "[make_mock_db] seeding visualize_mock_prod from $SEED_FILE"
"${PSQL[@]}" --single-transaction -d visualize_mock_prod < "$SEED_FILE"

# --- 4) read-only grants for reader in prod ----------------------------------
echo "[make_mock_db] granting SELECT to reader on prod public tables"
"${PSQL[@]}" -d visualize_mock_prod <<'SQL'
GRANT SELECT ON ALL TABLES IN SCHEMA public TO reader;
SQL

# --- 5) junk table in test ---------------------------------------------------
echo "[make_mock_db] creating legacy_junk in visualize_mock_test"
"${PSQL[@]}" -d visualize_mock_test <<'SQL'
DROP TABLE IF EXISTS legacy_junk;
CREATE TABLE legacy_junk(id int);
INSERT INTO legacy_junk VALUES (1), (2), (3);
SQL

echo "[make_mock_db] done"
