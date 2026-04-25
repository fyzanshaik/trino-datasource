#!/usr/bin/env python3
"""
Print Trino-visible metadata counts by catalog.

This intentionally queries Trino information_schema and SHOW CREATE TABLE, not
PostgreSQL or the Hive metastore directly, so it validates what the Atlan Trino
app will see.
"""

import os
import subprocess
import sys
from dataclasses import dataclass


TRINO_CONTAINER = os.environ.get("TRINO_CONTAINER", "trino")
CATALOGS_ENV = os.environ.get("TRINO_VALIDATE_CATALOGS")
MAX_PARTITION_SCAN = int(os.environ.get("TRINO_VALIDATE_MAX_PARTITION_SCAN", "1000"))


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def trino(query: str) -> str:
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            TRINO_CONTAINER,
            "trino",
            "--output-format",
            "TSV",
            "--execute",
            query,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def scalar(query: str) -> int:
    output = trino(query)
    if not output:
        return 0
    return int(output.splitlines()[0])


def rows(query: str) -> list[tuple[str, ...]]:
    output = trino(query)
    if not output:
        return []
    return [tuple(line.split("\t")) for line in output.splitlines()]


def visible_catalogs() -> list[str]:
    if CATALOGS_ENV:
        return [catalog.strip() for catalog in CATALOGS_ENV.split(",") if catalog.strip()]

    catalogs = [row[0] for row in rows("SHOW CATALOGS")]
    return [catalog for catalog in catalogs if catalog != "system"]


def partitioned_table_count(catalog: str, base_tables: int) -> tuple[str, str]:
    if "hive" not in catalog and "iceberg" not in catalog:
        return "0", ""
    if base_tables > MAX_PARTITION_SCAN:
        return "skipped", f">{MAX_PARTITION_SCAN} base tables"

    catalog_q = quote_identifier(catalog)
    table_rows = rows(
        f"""
        SELECT table_schema, table_name
        FROM {catalog_q}.information_schema.tables
        WHERE table_schema <> 'information_schema'
          AND table_type = 'BASE TABLE'
        ORDER BY table_schema, table_name
        """
    )

    count = 0
    errors = 0
    for schema, table in table_rows:
        name = ".".join(
            [quote_identifier(catalog), quote_identifier(schema), quote_identifier(table)]
        )
        try:
            ddl = trino(f"SHOW CREATE TABLE {name}")
        except RuntimeError:
            errors += 1
            continue
        if "partitioned_by = ARRAY[" in ddl or "partitioning = ARRAY[" in ddl:
            count += 1

    note = f"{errors} SHOW CREATE failures" if errors else ""
    return str(count), note


@dataclass
class CatalogCounts:
    catalog: str
    schemas: int
    tables: int
    views: int
    columns: int
    partitioned_tables: str
    note: str


def count_catalog(catalog: str) -> CatalogCounts:
    catalog_q = quote_identifier(catalog)
    schemas = scalar(
        f"""
        SELECT count(*)
        FROM {catalog_q}.information_schema.schemata
        WHERE schema_name <> 'information_schema'
        """
    )
    tables = scalar(
        f"""
        SELECT count(*)
        FROM {catalog_q}.information_schema.tables
        WHERE table_schema <> 'information_schema'
          AND table_type = 'BASE TABLE'
        """
    )
    views = scalar(
        f"""
        SELECT count(*)
        FROM {catalog_q}.information_schema.tables
        WHERE table_schema <> 'information_schema'
          AND table_type = 'VIEW'
        """
    )
    columns = scalar(
        f"""
        SELECT count(*)
        FROM {catalog_q}.information_schema.columns
        WHERE table_schema <> 'information_schema'
        """
    )
    partitioned_tables, note = partitioned_table_count(catalog, tables)
    return CatalogCounts(catalog, schemas, tables, views, columns, partitioned_tables, note)


def main() -> int:
    try:
        catalogs = visible_catalogs()
        counts = [count_catalog(catalog) for catalog in catalogs]
    except (RuntimeError, ValueError) as exc:
        print(f"metadata validation failed: {exc}", file=sys.stderr)
        return 1

    print(f"Trino container: {TRINO_CONTAINER}")
    print(f"Visible catalogs excluding system: {len(catalogs)}")
    print()
    print(
        f"{'catalog':<18} {'schemas':>8} {'tables':>8} {'views':>8} "
        f"{'columns':>10} {'partitioned':>12}  note"
    )
    print("-" * 86)
    for item in counts:
        print(
            f"{item.catalog:<18} {item.schemas:>8} {item.tables:>8} "
            f"{item.views:>8} {item.columns:>10} "
            f"{item.partitioned_tables:>12}  {item.note}"
        )

    print()
    print("Default fixture shape to expect after setup:")
    print("- postgres keeps 20 bulk schemas, 10,000 bulk tables, and 510,000 bulk columns")
    print("- hive adds configurable feature schemas/tables plus partitioned baseline tables")
    print("- iceberg adds configurable partition-transform tables plus baseline dim_customer")
    return 0


if __name__ == "__main__":
    sys.exit(main())
