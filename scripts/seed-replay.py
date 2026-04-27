#!/usr/bin/env python3
"""Apply the anonymized replay corpus to a running Trino instance.

Reads:
  replay/source_tables.json   anonymized source tables (catalog, schema, name, columns)
  replay/views.json           anonymized view DDLs (catalog, schema, name, definition)

Steps:
  1. Connect to Trino at TRINO_HOST:TRINO_PORT.
  2. CREATE SCHEMA for each catalog/schema referenced.
  3. CREATE TABLE for each source table — empty Hive tables backed by S3.
  4. Topologically sort views (one view may reference another) and apply
     CREATE VIEW with the verbatim anonymized DDL.

Idempotency: each step uses CREATE … IF NOT EXISTS where Trino allows. For
views we DROP VIEW IF EXISTS first because Trino's CREATE OR REPLACE VIEW
respects column types and we want to be permissive when re-running.

Usage:
    python3 scripts/seed-replay.py
    python3 scripts/seed-replay.py --dry-run                # print SQL only
    python3 scripts/seed-replay.py --host trino.local --port 8080
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    import sqlglot
    from sqlglot import expressions as exp
    import trino
except ImportError:
    sys.stderr.write(
        "trino-python-client + sqlglot required. pip install -r requirements.txt\n"
    )
    sys.exit(2)


REPO_ROOT = Path(__file__).resolve().parent.parent
REPLAY_DIR = REPO_ROOT / "replay"
SOURCE_TABLES_PATH = REPLAY_DIR / "source_tables.json"
VIEWS_PATH = REPLAY_DIR / "views.json"


def load_jsonl(path: Path) -> list[dict]:
    out = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def connect(host: str, port: int) -> "trino.dbapi.Connection":
    return trino.dbapi.connect(
        host=host,
        port=port,
        user="trino",
        catalog="analytics",
    )


def ensure_schemas(cur, tables: list[dict], views: list[dict], dry_run: bool) -> None:
    """Create every catalog.schema pair referenced anywhere.

    Hive Metastore won't auto-pick a default location, so each schema gets
    an explicit S3 path on the per-catalog MinIO bucket. Buckets named
    analytics, events, archive are pre-created by the minio-setup container.
    """
    pairs: set[tuple[str, str]] = set()
    for t in tables:
        pairs.add((t["catalog"], t["schema"]))
    for v in views:
        pairs.add((v["catalog"], v["schema"]))
    for cat, sch in sorted(pairs):
        location = f"s3a://{cat}/{sch}/"
        sql = (
            f'CREATE SCHEMA IF NOT EXISTS "{cat}"."{sch}" '
            f"WITH (location = '{location}')"
        )
        print(f"  {sql}")
        if not dry_run:
            cur.execute(sql)
            cur.fetchall()


def ensure_source_tables(cur, tables: list[dict], dry_run: bool) -> None:
    """Create every source table as an empty Hive table."""
    for t in sorted(tables, key=lambda x: (x["catalog"], x["schema"], x["name"])):
        cols_sql = ",\n    ".join(
            f'"{c["name"]}" {c["type"]}' for c in t["columns"]
        )
        sql = (
            f'CREATE TABLE IF NOT EXISTS "{t["catalog"]}"."{t["schema"]}"."{t["name"]}" (\n'
            f"    {cols_sql}\n"
            ")"
        )
        print(f"  {t['catalog']}.{t['schema']}.{t['name']}  ({len(t['columns'])} cols)")
        if not dry_run:
            cur.execute(sql)
            cur.fetchall()


def topo_sort_views(views: list[dict]) -> list[dict]:
    """Sort views so that a view referencing another is created after its ref.

    Detects view-on-view references by walking the SQL AST and looking up
    referenced table names against the set of known view names.
    """
    view_names = {v["name"] for v in views}
    deps: dict[str, set[str]] = {v["name"]: set() for v in views}
    for v in views:
        ast = sqlglot.parse_one(v["definition"], dialect="trino")
        for t in ast.find_all(exp.Table):
            if t.name == v["name"]:
                continue
            if t.name in view_names:
                deps[v["name"]].add(t.name)

    # Kahn's algorithm
    sorted_names: list[str] = []
    pending = {n: set(d) for n, d in deps.items()}
    while pending:
        ready = sorted([n for n, d in pending.items() if not d])
        if not ready:
            cyclic = sorted(pending.keys())
            raise RuntimeError(f"Cyclic view deps among: {cyclic}")
        for n in ready:
            sorted_names.append(n)
            del pending[n]
            for other in pending.values():
                other.discard(n)

    name_to_view = {v["name"]: v for v in views}
    return [name_to_view[n] for n in sorted_names]


def ensure_views(cur, views: list[dict], dry_run: bool) -> int:
    """Apply every CREATE VIEW in dependency order. Returns count succeeded."""
    ordered = topo_sort_views(views)
    succeeded = 0
    failed: list[tuple[str, str]] = []
    for v in ordered:
        drop_sql = f'DROP VIEW IF EXISTS "{v["catalog"]}"."{v["schema"]}"."{v["name"]}"'
        print(f"  {v['catalog']}.{v['schema']}.{v['name']}")
        if not dry_run:
            try:
                cur.execute(drop_sql)
                cur.fetchall()
            except Exception:
                pass
            try:
                cur.execute(v["definition"])
                cur.fetchall()
                succeeded += 1
            except Exception as e:
                # Record and continue. Some FreeWheel-original SQL has implicit
                # qualifications that sqlglot's re-emit doesn't preserve
                # perfectly. The remaining views are still useful for testing.
                msg = str(e).split("query_id=")[0].strip()
                msg = msg[:200]
                failed.append((f"{v['catalog']}.{v['schema']}.{v['name']}", msg))
                print(f"    ! skipped: {msg[:120]}")
        else:
            succeeded += 1
    if failed:
        print(f"\n  WARNING: {len(failed)} view(s) failed to create:")
        for name, msg in failed:
            print(f"    {name}: {msg[:120]}")
    return succeeded


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default=os.environ.get("TRINO_HOST", "localhost"))
    p.add_argument("--port", type=int, default=int(os.environ.get("TRINO_PORT", "8080")))
    p.add_argument("--dry-run", action="store_true",
                   help="Print SQL but don't execute")
    args = p.parse_args()

    if not SOURCE_TABLES_PATH.exists() or not VIEWS_PATH.exists():
        print(
            f"ERROR: replay assets not found. Run extract-replay-data.py first.",
            file=sys.stderr,
        )
        return 2

    tables = load_jsonl(SOURCE_TABLES_PATH)
    views = load_jsonl(VIEWS_PATH)
    print(f"loaded: {len(tables)} source tables, {len(views)} views")

    if args.dry_run:
        print("\n[dry-run mode — no SQL will be executed]\n")
        cur = None
    else:
        print(f"connecting to trino://{args.host}:{args.port}")
        conn = connect(args.host, args.port)
        cur = conn.cursor()

    print("\n=== Schemas ===")
    ensure_schemas(cur, tables, views, args.dry_run)

    print("\n=== Source tables ===")
    ensure_source_tables(cur, tables, args.dry_run)

    print("\n=== Views (topological order) ===")
    n_views_ok = ensure_views(cur, views, args.dry_run)

    print(f"\nDone. {len(tables)} tables + {n_views_ok}/{len(views)} views applied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
