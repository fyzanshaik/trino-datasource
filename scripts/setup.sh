#!/usr/bin/env bash
# Full environment setup — run once after `docker compose up -d`.
#
# Steps:
#   1. Wait for postgres + Trino to be healthy.
#   2. Generate 10 000 bulk tables in postgres (~5 min).
#   3. Populate prod_sales.large with 1 M rows (~15 s).
#   4. Create baseline Hive schemas + partitioned tables via Trino.
#   5. Create baseline Iceberg schemas + tables via Trino.
#   6. Create configurable Trino-specific Hive/Iceberg feature coverage.
#   7. Generate the password.db for the basic-auth Trino (optional).

set -euo pipefail

POSTGRES_CONTAINER=postgres
TRINO_CONTAINER=trino

# Basic-auth credentials for trino-basic. Override at runtime:
#   TRINO_BASIC_USER=myuser TRINO_BASIC_PASSWORD='s3cret' ./scripts/setup.sh
TRINO_BASIC_USER="${TRINO_BASIC_USER:-testuser}"
TRINO_BASIC_PASSWORD="${TRINO_BASIC_PASSWORD:-testpass}"

# Moderate Trino-specific feature coverage. Override to scale up/down:
#   TRINO_HIVE_FEATURE_SCHEMAS=8 TRINO_HIVE_TABLES_PER_SCHEMA=12 ./scripts/setup.sh
export TRINO_HIVE_FEATURE_SCHEMAS="${TRINO_HIVE_FEATURE_SCHEMAS:-4}"
export TRINO_HIVE_TABLES_PER_SCHEMA="${TRINO_HIVE_TABLES_PER_SCHEMA:-6}"
export TRINO_ICEBERG_FEATURE_SCHEMAS="${TRINO_ICEBERG_FEATURE_SCHEMAS:-3}"
export TRINO_ICEBERG_TABLES_PER_SCHEMA="${TRINO_ICEBERG_TABLES_PER_SCHEMA:-4}"

# ── helpers ──────────────────────────────────────────────────────────────────

pg() {
  docker exec -i "$POSTGRES_CONTAINER" psql -U trino -d trino "$@"
}

trino_exec() {
  docker exec -i "$TRINO_CONTAINER" trino
}

wait_for() {
  local name=$1 check=$2
  echo -n "Waiting for $name"
  until eval "$check" >/dev/null 2>&1; do
    echo -n '.'
    sleep 3
  done
  echo " ready."
}

# ── 1. Wait ───────────────────────────────────────────────────────────────────

wait_for postgres \
  "docker exec $POSTGRES_CONTAINER pg_isready -U trino"

wait_for trino \
  "docker exec $TRINO_CONTAINER curl -sf http://localhost:8080/v1/info"

# ── 2. Bulk table generation ─────────────────────────────────────────────────

echo "=== Generating bulk schemas + tables (10 000 tables, ~5 min) ==="
pg < "$(dirname "$0")/bulk-generate.sql"
echo "Bulk generation done."

# ── 3. Populate large table ──────────────────────────────────────────────────

echo "=== Populating prod_sales.large with 1 M rows (~15 s) ==="
pg < "$(dirname "$0")/populate-large.sql"
echo "Large table populated."

# ── 4. Hive tables ───────────────────────────────────────────────────────────

echo "=== Creating Hive schemas + tables ==="
trino_exec < "$(dirname "$0")/trino-hive.sql"
echo "Hive done."

# ── 5. Iceberg tables ────────────────────────────────────────────────────────

echo "=== Creating Iceberg schemas + tables ==="
trino_exec < "$(dirname "$0")/trino-iceberg.sql"
echo "Iceberg done."

# ── 6. Trino feature coverage ────────────────────────────────────────────────

echo "=== Creating Trino-specific feature coverage ==="
python3 "$(dirname "$0")/trino-feature-generate.py" | trino_exec
echo "Trino feature coverage done."

# ── 7. password.db for basic-auth Trino (optional) ───────────────────────────

echo "=== Generating basic-auth password.db ==="
if command -v htpasswd >/dev/null 2>&1; then
  htpasswd -nbB -C 10 "$TRINO_BASIC_USER" "$TRINO_BASIC_PASSWORD" > trino/basic-auth/password.db
  echo "password.db written (user: $TRINO_BASIC_USER)."
else
  echo "htpasswd not found — install apache2-utils / httpd-tools and run:"
  echo "  htpasswd -nbB $TRINO_BASIC_USER '<password>' > trino/basic-auth/password.db"
  echo "Then restart trino-basic: docker compose --profile auth-test up -d trino-basic"
fi

# ── 8. JWT HMAC signing key for trino-basic (optional, idempotent) ───────────
# trino-basic accepts both PASSWORD and JWT. The HMAC secret signs HS256 tokens.
# Mint a token with: ./scripts/generate-jwt.sh

JWT_KEY_FILE="trino/basic-auth/jwt-key"
echo "=== Ensuring JWT signing key ==="
if [[ -s "$JWT_KEY_FILE" ]]; then
  echo "$JWT_KEY_FILE already exists — leaving in place."
else
  # Strip trailing newline so the key seen by `cat` (in generate-jwt.sh)
  # matches the bytes Trino reads from the file.
  openssl rand -base64 48 | tr -d '\n' > "$JWT_KEY_FILE"
  chmod 600 "$JWT_KEY_FILE"
  echo "$JWT_KEY_FILE written."
fi
echo "Mint a token: ./scripts/generate-jwt.sh [subject] [ttl-seconds]"

echo ""
echo "=== Setup complete ==="
echo "  Trino (no-auth):   http://localhost:8080"
echo "  Trino (basic-auth): http://localhost:8081  [start with: docker compose --profile auth-test up -d]"
echo "  MinIO console:      http://localhost:9001  (minio / minio123)"
echo ""
echo "  Tenant-facing Atlan crawl target: host=localhost port=8081 catalog=postgres/hive/iceberg auth=basic"
echo "  Use no-auth port 8080 only for local setup and validation."
