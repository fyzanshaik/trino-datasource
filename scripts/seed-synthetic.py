#!/usr/bin/env python3
"""Seed synthetic baseline tables + lineage views the replay corpus doesn't cover.

Two modes of synthetic content:

  baseline:  N schemas × M tables × P columns. Plain unrelated tables. Pads
             the asset count if you need to land in a specific range. Default
             knobs land somewhere around +3k columns.

  lineage:   K extra views exercising patterns not present in the replay
             corpus (CTEs, window functions, view-on-view chains). Each
             references baseline tables for source columns.

Driven entirely by env vars (defaults in .env.example).

Usage:
    python3 scripts/seed-synthetic.py
    python3 scripts/seed-synthetic.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    import trino
except ImportError:
    sys.stderr.write("trino-python-client required. pip install -r requirements.txt\n")
    sys.exit(2)


PRIMARY_CATALOG = os.environ.get("REPLAY_PRIMARY_CATALOG", "analytics")
SYNTH_SCHEMA_PREFIX = "synth"

# Defaults tuned to land in 10–15k assets with replay
N_BASELINE_SCHEMAS = int(os.environ.get("SYNTHETIC_BASELINE_SCHEMAS", "5"))
N_BASELINE_TABLES_PER_SCHEMA = int(os.environ.get("SYNTHETIC_BASELINE_TABLES_PER_SCHEMA", "8"))
N_BASELINE_COLS_PER_TABLE = int(os.environ.get("SYNTHETIC_BASELINE_COLUMNS_PER_TABLE", "70"))
N_LINEAGE_VIEWS = int(os.environ.get("SYNTHETIC_LINEAGE_VIEWS", "10"))


def _column_def(idx: int) -> str:
    """Generate a synthetic column definition. Mix some types for realism."""
    # Trino-native types only (Hive connector translates them).
    types = ["bigint", "varchar", "double", "boolean", "timestamp", "integer", "varchar"]
    t = types[idx % len(types)]
    return f'"sc_{idx:04d}" {t}'


def synthetic_baseline_ddls() -> list[tuple[str, str]]:
    """Returns list of (label, sql) for baseline schemas + tables."""
    out: list[tuple[str, str]] = []
    for s in range(N_BASELINE_SCHEMAS):
        schema = f"{SYNTH_SCHEMA_PREFIX}_baseline_{s+1:02d}"
        out.append((
            f"schema {schema}",
            f'CREATE SCHEMA IF NOT EXISTS "{PRIMARY_CATALOG}"."{schema}" '
            f"WITH (location = 's3a://{PRIMARY_CATALOG}/{schema}/')",
        ))
        for t in range(N_BASELINE_TABLES_PER_SCHEMA):
            tbl = f"syn_T{s+1:02d}_{t+1:03d}"
            cols = ",\n    ".join(_column_def(i) for i in range(N_BASELINE_COLS_PER_TABLE))
            sql = (
                f'CREATE TABLE IF NOT EXISTS "{PRIMARY_CATALOG}"."{schema}"."{tbl}" (\n'
                f"    {cols}\n)"
            )
            out.append((f"table {schema}.{tbl} ({N_BASELINE_COLS_PER_TABLE} cols)", sql))
    return out


def synthetic_lineage_views() -> list[tuple[str, str]]:
    """Synthetic views exercising patterns the replay corpus doesn't cover.

    Each view references a baseline table — we know they exist after the
    baseline DDL runs. Patterns:

      vw_syn_001..003  CTE + simple aggregation
      vw_syn_004..006  Window function (ROW_NUMBER, RANK)
      vw_syn_007..008  View-on-view (vw_syn_007 reads from vw_syn_001)
      vw_syn_009..010  Multi-table JOIN with CASE expressions
    """
    schema = f"{SYNTH_SCHEMA_PREFIX}_lineage"
    out: list[tuple[str, str]] = [
        (
            f"schema {schema}",
            f'CREATE SCHEMA IF NOT EXISTS "{PRIMARY_CATALOG}"."{schema}" '
            f"WITH (location = 's3a://{PRIMARY_CATALOG}/{schema}/')",
        ),
    ]

    # The first baseline schema/table — guaranteed to exist after baseline runs
    base_schema = f"{SYNTH_SCHEMA_PREFIX}_baseline_01"
    base_t1 = "syn_T01_001"
    base_t2 = "syn_T01_002"

    # CTE views — count up to lineage_views // 4
    cte_count = max(1, N_LINEAGE_VIEWS // 4)
    win_count = max(1, N_LINEAGE_VIEWS // 4)
    voov_count = max(1, N_LINEAGE_VIEWS // 4)
    join_count = max(1, N_LINEAGE_VIEWS - cte_count - win_count - voov_count)

    for i in range(cte_count):
        v = f"vw_syn_cte_{i+1:03d}"
        sql = f"""
CREATE VIEW "{PRIMARY_CATALOG}"."{schema}"."{v}" AS
WITH first_pass AS (
    SELECT "sc_0000" AS group_id, "sc_0005" AS metric_a, "sc_0005" AS metric_b
    FROM "{PRIMARY_CATALOG}"."{base_schema}"."{base_t1}"
    WHERE "sc_0007" IS NOT NULL
)
SELECT
    group_id,
    SUM(metric_a) AS sum_metric_a,
    AVG(metric_b) AS avg_metric_b
FROM first_pass
GROUP BY group_id
""".strip()
        out.append((f"view {schema}.{v} (CTE)", sql))

    for i in range(win_count):
        v = f"vw_syn_window_{i+1:03d}"
        sql = f"""
CREATE VIEW "{PRIMARY_CATALOG}"."{schema}"."{v}" AS
SELECT
    "sc_0000" AS partition_key,
    "sc_0005" AS event_value,
    ROW_NUMBER() OVER (PARTITION BY "sc_0000" ORDER BY "sc_0005" DESC) AS rn,
    RANK() OVER (PARTITION BY "sc_0000" ORDER BY "sc_0005" DESC) AS rnk
FROM "{PRIMARY_CATALOG}"."{base_schema}"."{base_t1}"
""".strip()
        out.append((f"view {schema}.{v} (window)", sql))

    # View-on-view: read from first CTE view
    for i in range(voov_count):
        v = f"vw_syn_voov_{i+1:03d}"
        upstream = f"vw_syn_cte_{(i % cte_count) + 1:03d}"
        sql = f"""
CREATE VIEW "{PRIMARY_CATALOG}"."{schema}"."{v}" AS
SELECT
    group_id,
    sum_metric_a,
    avg_metric_b,
    sum_metric_a + avg_metric_b AS combined
FROM "{PRIMARY_CATALOG}"."{schema}"."{upstream}"
WHERE sum_metric_a > 0
""".strip()
        out.append((f"view {schema}.{v} (view-on-view→{upstream})", sql))

    # Multi-table JOIN
    for i in range(join_count):
        v = f"vw_syn_join_{i+1:03d}"
        sql = f"""
CREATE VIEW "{PRIMARY_CATALOG}"."{schema}"."{v}" AS
SELECT
    a."sc_0000" AS join_key,
    a."sc_0005" AS left_metric,
    b."sc_0005" AS right_metric,
    CASE
        WHEN a."sc_0007" IS NULL THEN -1
        WHEN a."sc_0007" > b."sc_0007" THEN 1
        ELSE 0
    END AS comparison_flag
FROM "{PRIMARY_CATALOG}"."{base_schema}"."{base_t1}" AS a
INNER JOIN "{PRIMARY_CATALOG}"."{base_schema}"."{base_t2}" AS b
    ON a."sc_0000" = b."sc_0000"
""".strip()
        out.append((f"view {schema}.{v} (join)", sql))

    return out


def execute_each(cur, items: list[tuple[str, str]], dry_run: bool) -> int:
    n = 0
    for label, sql in items:
        print(f"  {label}")
        if not dry_run:
            cur.execute(sql)
            cur.fetchall()
        n += 1
    return n


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default=os.environ.get("TRINO_HOST", "localhost"))
    p.add_argument("--port", type=int, default=int(os.environ.get("TRINO_PORT", "8080")))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-baseline", action="store_true",
                   help="Only emit the lineage views, not the padding tables")
    p.add_argument("--skip-lineage", action="store_true",
                   help="Only emit the padding tables, not the lineage views")
    args = p.parse_args()

    enabled = os.environ.get("ENABLE_SYNTHETIC", "true").lower() == "true"
    if not enabled and not args.dry_run:
        print("ENABLE_SYNTHETIC=false, skipping synthetic seed.")
        return 0

    if args.dry_run:
        print("[dry-run mode]")
        cur = None
    else:
        print(f"connecting to trino://{args.host}:{args.port}")
        cur = trino.dbapi.connect(host=args.host, port=args.port,
                                   user="trino", catalog=PRIMARY_CATALOG).cursor()

    if not args.skip_baseline:
        print("\n=== Synthetic baseline ===")
        baseline = synthetic_baseline_ddls()
        n = execute_each(cur, baseline, args.dry_run)
        n_schemas = sum(1 for lbl, _ in baseline if lbl.startswith("schema "))
        n_tables = n - n_schemas
        n_cols = n_tables * N_BASELINE_COLS_PER_TABLE
        print(f"  → {n_schemas} schemas, {n_tables} tables, ~{n_cols} columns")

    if not args.skip_lineage:
        print("\n=== Synthetic lineage views ===")
        lineage = synthetic_lineage_views()
        n = execute_each(cur, lineage, args.dry_run)
        print(f"  → {n - 1} views (1 schema)")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
