#!/usr/bin/env bash
#
# End-to-end setup. Bring up containers, seed replay corpus, optionally
# seed synthetic padding, validate counts.
#
# Usage:
#   ./scripts/setup.sh                  # full setup
#   ./scripts/setup.sh --skip-synthetic # only replay
#   ./scripts/setup.sh --rebuild        # tear down before bringing up
#
set -euo pipefail

cd "$(dirname "$0")/.."

# Load .env if present
if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

REBUILD=0
SKIP_SYNTHETIC=0
SKIP_REPLAY=0
for arg in "$@"; do
    case "$arg" in
        --rebuild)         REBUILD=1 ;;
        --skip-synthetic)  SKIP_SYNTHETIC=1 ;;
        --skip-replay)     SKIP_REPLAY=1 ;;
        *) echo "unknown arg: $arg" >&2; exit 2 ;;
    esac
done

# ── 1. Ensure replay assets exist ─────────────────────────────────────
if [[ ! -f replay/views.json || ! -f replay/source_tables.json ]]; then
    cat <<EOF >&2
ERROR: replay assets not found.

Run extract-replay-data.py first:

    python3 scripts/extract-replay-data.py \\
        --golden \${GOLDEN_DATASET_PATH:-../atlan-trino-app/new-golden-dataset} \\
        --out replay
EOF
    exit 1
fi

# ── 2. (Optional) tear down ───────────────────────────────────────────
if (( REBUILD )); then
    echo "=== Tearing down existing stack ==="
    docker compose down -v
fi

# ── 3. Bring up containers ────────────────────────────────────────────
# If a Cloudflare tunnel token is set in .env, also bring up the cloudflared
# service via the `tunnel` profile. This makes the full bring-up (Trino +
# public tunnel) a single command on machines that have a token configured.
COMPOSE_ARGS=()
TUNNEL_ENABLED=0
if [[ -n "${CLOUDFLARED_TOKEN:-}" ]]; then
    echo "=== CLOUDFLARED_TOKEN detected — enabling tunnel profile ==="
    COMPOSE_ARGS+=(--profile tunnel)
    TUNNEL_ENABLED=1
fi

echo "=== Starting containers ==="
docker compose "${COMPOSE_ARGS[@]}" up -d

echo "=== Waiting for Trino healthcheck ==="
# /v1/info returns 200 before catalogs finish loading, so we issue a real query
# (SHOW CATALOGS) and only proceed once Trino accepts it.
TRINO_PORT="${TRINO_PORT:-8080}"
for i in {1..90}; do
    body=$(curl -s -H 'X-Trino-User: trino' -H 'X-Trino-Catalog: analytics' \
                -H 'Content-Type: text/plain' \
                -X POST --data 'SHOW CATALOGS' \
                "http://localhost:${TRINO_PORT}/v1/statement" 2>/dev/null || true)
    if [[ -n "$body" ]] && ! echo "$body" | grep -q "SERVER_STARTING_UP"; then
        echo "  Trino is up and serving queries."
        break
    fi
    sleep 2
    if (( i == 90 )); then
        echo "ERROR: Trino did not accept queries in 180s." >&2
        docker compose logs --tail=50 trino >&2
        exit 1
    fi
done

# ── 4. Activate venv ──────────────────────────────────────────────────
if [[ ! -d .venv ]]; then
    echo "=== Creating .venv ==="
    python3 -m venv .venv
fi
.venv/bin/pip install -q -r requirements.txt

PY=.venv/bin/python

# ── 5. Seed replay ────────────────────────────────────────────────────
if (( ! SKIP_REPLAY )); then
    echo "=== Seeding replay corpus ==="
    "$PY" scripts/seed-replay.py
fi

# ── 6. Seed synthetic ─────────────────────────────────────────────────
if (( ! SKIP_SYNTHETIC )) && [[ "${ENABLE_SYNTHETIC:-true}" == "true" ]]; then
    echo "=== Seeding synthetic ==="
    "$PY" scripts/seed-synthetic.py
fi

# ── 7. Validate ───────────────────────────────────────────────────────
echo "=== Validating counts ==="
"$PY" scripts/validate-counts.py || true   # don't hard-fail on count drift

# ── 8. Tunnel readiness (if enabled) ──────────────────────────────────
if (( TUNNEL_ENABLED )); then
    echo "=== Waiting for Cloudflare tunnel to register ==="
    # cloudflared logs "Registered tunnel connection" once each connector is
    # connected to a Cloudflare edge POP. We poll for up to 60s.
    for i in {1..30}; do
        if docker logs cloudflared-lineage 2>&1 | grep -q "Registered tunnel connection"; then
            echo "  Tunnel registered."
            break
        fi
        sleep 2
        if (( i == 30 )); then
            echo "  WARN: tunnel did not register in 60s. Check 'docker compose logs cloudflared'." >&2
        fi
    done
fi

echo
echo "Done. Trino is at http://localhost:${TRINO_PORT:-8080} (user: trino, no password)"
echo "Catalogs: analytics"
if (( TUNNEL_ENABLED )); then
    echo
    echo "Cloudflare tunnel is up. Public hostname is whatever you configured in"
    echo "Cloudflare Zero Trust → Tunnels → (this tunnel) → Public Hostnames."
    echo "Inspect with: docker compose logs --tail=20 cloudflared"
fi
