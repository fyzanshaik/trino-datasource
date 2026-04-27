#!/usr/bin/env bash
#
# Run the existing trino-app parity scripts against the local fixture.
#
# Re-uses the QI parity scripts at ../atlan-trino-app/parity/. Those scripts
# read from the local fixture's view DDLs (via system.jdbc / SHOW CREATE VIEW)
# and structurally compare against the asset shape we expect:
#   23 views, 2244 source-bearing column edges, 23 Process entities.
#
# This is a smoke test, NOT a byte-level diff — the original golden dataset
# uses different identifiers, so byte parity isn't possible by design.
#
# Usage:
#   ./scripts/run-parity.sh
#
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

PY=.venv/bin/python
[[ -x "$PY" ]] || { echo "ERROR: run setup.sh first to create .venv"; exit 1; }

echo "=== 1. Confirm replay views are visible to Trino ==="
"$PY" scripts/validate-counts.py --catalog analytics || true

echo
echo "=== 2. Pull each view's DDL from Trino, run QI parser, count source-bearing edges ==="
"$PY" - <<'PYEOF'
import json, os, subprocess, sys
from pathlib import Path

import trino

# Try to use QI's parser via uv if available; otherwise use sqlglot directly
try:
    from app.parsers.sqlglot_parser.lineage.lineage import parse_statement, sqlglot_lineage, to_gudusoft_output  # type: ignore
    USE_QI = True
except ImportError:
    USE_QI = False
    import sqlglot

host = os.environ.get("TRINO_HOST", "localhost")
port = int(os.environ.get("TRINO_PORT", "8080"))
cur = trino.dbapi.connect(host=host, port=port, user="trino", catalog="analytics").cursor()

# Discover replay views (catalog=analytics, schema starting with mart_)
cur.execute("""
    SELECT table_cat, table_schem, table_name
    FROM system.jdbc.tables
    WHERE table_type = 'VIEW' AND table_cat = 'analytics' AND table_schem LIKE 'mart_%'
    ORDER BY 1,2,3
""")
view_refs = cur.fetchall()
print(f"Discovered {len(view_refs)} replay views.")

import sqlglot
dialect = sqlglot.Dialect.get_or_raise("trino")
total_src = 0
total_views = 0
fails = 0
for cat, sch, name in view_refs:
    cur.execute(f'SHOW CREATE VIEW "{cat}"."{sch}"."{name}"')
    row = cur.fetchone()
    sql = row[0] if row else ""
    if not sql:
        fails += 1
        continue
    try:
        if USE_QI:
            bpr = parse_statement(sql, dialect, cat, sch)
            lineage = sqlglot_lineage(bpr, {})
            g = to_gudusoft_output(lineage)
            n_src = sum(1 for r in g.get("relationships", []) if r.get("sources"))
        else:
            # Fallback: use sqlglot to count column projections (less accurate)
            ast = sqlglot.parse_one(sql, dialect="trino")
            n_src = len(list(ast.find_all(sqlglot.exp.Column)))
        total_src += n_src
        total_views += 1
    except Exception as e:
        print(f"  FAIL {name}: {e}")
        fails += 1

print(f"\nViews parsed:                  {total_views}")
print(f"Total source-bearing edges:    {total_src}")
print(f"Parser failures:               {fails}")

# Expected (from earlier parity run on the original FreeWheel data — unchanged
# by anonymization since structure is preserved)
expected_views = 23
expected_edges = 2244
ok = total_views == expected_views and total_src == expected_edges
print(f"\nExpected: {expected_views} views, {expected_edges} edges")
if ok:
    print("PARITY: OK")
else:
    print("PARITY: DRIFT")
    sys.exit(1)
PYEOF
