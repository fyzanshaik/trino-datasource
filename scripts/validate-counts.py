#!/usr/bin/env python3
"""Verify the seeded fixture by querying Trino's system tables.

Reports:
  - tables/views per (catalog, schema)
  - total tables, views, and columns visible
  - whether totals fall in the 10–15k asset target range

Usage:
    python3 scripts/validate-counts.py
    python3 scripts/validate-counts.py --catalog analytics
"""

from __future__ import annotations

import argparse
import os
import sys

try:
    import trino
except ImportError:
    sys.stderr.write("trino-python-client required. pip install -r requirements.txt\n")
    sys.exit(2)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default=os.environ.get("TRINO_HOST", "localhost"))
    p.add_argument("--port", type=int, default=int(os.environ.get("TRINO_PORT", "8080")))
    p.add_argument("--catalog", default=None,
                   help="Restrict to a single catalog (default: all visible)")
    args = p.parse_args()

    print(f"connecting to trino://{args.host}:{args.port}")
    cur = trino.dbapi.connect(host=args.host, port=args.port,
                               user="trino", catalog="analytics").cursor()

    catalog_filter = ""
    params: list = []
    if args.catalog:
        catalog_filter = "WHERE table_cat = ?"
        params = [args.catalog]

    # Count tables/views per (catalog, schema). Use system.jdbc.tables — same
    # interface the Atlan Trino app uses.
    cur.execute(f"""
        SELECT table_cat, table_schem, table_type, count(*) AS n
        FROM system.jdbc.tables
        {catalog_filter}
        GROUP BY 1, 2, 3
        ORDER BY 1, 2, 3
    """, params)
    rows = cur.fetchall()

    print("\n=== Tables and views per schema ===")
    print(f"{'catalog':<20} {'schema':<25} {'type':<10} {'count':>7}")
    by_schema: dict[tuple[str, str], dict[str, int]] = {}
    for cat, sch, kind, n in rows:
        # Skip Trino's internal schemas
        if sch in ("information_schema", "metadata"):
            continue
        # Skip standard internal catalogs
        if cat in ("system", "jmx"):
            continue
        print(f"{cat:<20} {sch:<25} {kind:<10} {n:>7}")
        by_schema.setdefault((cat, sch), {})[kind] = n

    n_tables = sum(s.get("TABLE", 0) for s in by_schema.values())
    n_views = sum(s.get("VIEW", 0) for s in by_schema.values())
    print(f"\n  total tables: {n_tables}")
    print(f"  total views:  {n_views}")

    # Count columns
    cur.execute(f"""
        SELECT count(*) FROM system.jdbc.columns
        {catalog_filter}
    """, params)
    n_columns = cur.fetchone()[0]
    print(f"  total columns: {n_columns}")

    n_schemas = len(by_schema)
    n_catalogs = len({c for c, _ in by_schema})
    total_assets = n_catalogs + n_schemas + n_tables + n_views + n_columns

    print(f"\n=== Asset roll-up ===")
    print(f"  catalogs: {n_catalogs}")
    print(f"  schemas:  {n_schemas}")
    print(f"  tables:   {n_tables}")
    print(f"  views:    {n_views}")
    print(f"  columns:  {n_columns}")
    print(f"  TOTAL ASSETS: {total_assets}")

    print(f"\n=== Target check ===")
    target_lo, target_hi = 10_000, 15_000
    if target_lo <= total_assets <= target_hi:
        print(f"  ✓ {total_assets} is within target range {target_lo}–{target_hi}")
        return 0
    elif total_assets < target_lo:
        print(f"  ⚠ {total_assets} below target {target_lo}. Increase synthetic baseline.")
        return 1
    else:
        print(f"  ⚠ {total_assets} above target {target_hi}. Decrease synthetic baseline.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
