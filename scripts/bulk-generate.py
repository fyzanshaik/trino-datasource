#!/usr/bin/env python3
"""
Generate 20 schemas x 500 tables x 51 columns = 510 000 column assets in postgres.
Each CREATE TABLE is its own autocommit transaction — no shared-memory / lock issues.

Usage:
    python3 scripts/bulk-generate.py
    DOCKER_HOST=unix:///path/to/docker.sock python3 scripts/bulk-generate.py
"""

import subprocess
import sys
import os

DOCKER_HOST = os.environ.get("DOCKER_HOST", f"unix://{os.path.expanduser('~')}/.orbstack/run/docker.sock")
SCHEMAS = 20
TABLES_PER_SCHEMA = 500


def build_sql() -> str:
    lines = []
    for s in range(1, SCHEMAS + 1):
        schema = f"bulk_{s:03d}"
        lines.append(f"CREATE SCHEMA IF NOT EXISTS {schema};")
        for t in range(1, TABLES_PER_SCHEMA + 1):
            cols = ["id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY"]
            for i in range(1, 11):
                cols.append(f"vc_{i:02d} VARCHAR(128)")
            for i in range(1, 11):
                cols.append(f"n_{i:02d}  INT")
            for i in range(1, 11):
                cols.append(f"d_{i:02d}  DECIMAL(12,4)")
            for i in range(1, 11):
                cols.append(f"ts_{i:02d} TIMESTAMP")
            for i in range(1, 11):
                cols.append(f"b_{i:02d}  BOOLEAN")
            lines.append(
                f"CREATE TABLE IF NOT EXISTS {schema}.tbl_{t:04d} ({', '.join(cols)});"
            )
        lines.append(f"\\echo schema {schema} done")
    return "\n".join(lines)


def main():
    print(f"Generating {SCHEMAS} schemas x {TABLES_PER_SCHEMA} tables x 51 cols "
          f"= {SCHEMAS * TABLES_PER_SCHEMA * 51:,} column assets …")
    sql = build_sql()
    result = subprocess.run(
        [
            "docker", "--host", DOCKER_HOST,
            "exec", "-i", "postgres",
            "psql", "-U", "trino", "-d", "trino",
        ],
        input=sql.encode(),
        capture_output=False,
    )
    if result.returncode != 0:
        print("psql exited with error", result.returncode, file=sys.stderr)
        sys.exit(result.returncode)
    print("Bulk generation complete.")


if __name__ == "__main__":
    main()
