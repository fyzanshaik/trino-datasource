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


def partitioned_table_count(
    catalog: str, base_tables: int
) -> tuple[str, str, str]:
    """Returns (ddl_partitioned, with_partition_rows, note)."""
    if "hive" not in catalog and "iceberg" not in catalog:
        return "0", "0", ""
    if base_tables > MAX_PARTITION_SCAN:
        return "skipped", "skipped", f">{MAX_PARTITION_SCAN} base tables"

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

    ddl_count = 0
    with_rows_count = 0
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
            ddl_count += 1
            partitions_relation = ".".join(
                [
                    quote_identifier(catalog),
                    quote_identifier(schema),
                    quote_identifier(table + "$partitions"),
                ]
            )
            try:
                rows_present = scalar(
                    f"SELECT count(*) FROM {partitions_relation}"
                )
            except RuntimeError:
                rows_present = 0
            if rows_present > 0:
                with_rows_count += 1

    note = f"{errors} SHOW CREATE failures" if errors else ""
    return str(ddl_count), str(with_rows_count), note


@dataclass
class CatalogCounts:
    catalog: str
    schemas: int
    visible_tables: int
    queryable_tables: int
    views: int
    columns: int
    ddl_partitioned: str
    with_partition_rows: str
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
    visible_tables = scalar(
        f"""
        SELECT count(*)
        FROM system.jdbc.tables
        WHERE table_cat = '{catalog}'
          AND table_schem <> 'information_schema'
          AND table_type IN ('TABLE', 'VIEW')
        """
    )
    queryable_tables = scalar(
        f"""
        SELECT count(DISTINCT (table_schem, table_name))
        FROM system.jdbc.columns
        WHERE table_cat = '{catalog}'
          AND table_schem <> 'information_schema'
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
    ddl_partitioned, with_partition_rows, note = partitioned_table_count(
        catalog, visible_tables
    )
    return CatalogCounts(
        catalog,
        schemas,
        visible_tables,
        queryable_tables,
        views,
        columns,
        ddl_partitioned,
        with_partition_rows,
        note,
    )


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
        f"{'catalog':<16} {'schemas':>7} {'visible':>8} {'queryable':>10} "
        f"{'ghosts':>7} {'views':>6} {'columns':>9} "
        f"{'DDL-part':>9} {'has-rows':>9}  note"
    )
    print("-" * 110)
    for item in counts:
        ghosts = item.visible_tables - item.queryable_tables
        print(
            f"{item.catalog:<16} {item.schemas:>7} {item.visible_tables:>8} "
            f"{item.queryable_tables:>10} {ghosts:>7} {item.views:>6} "
            f"{item.columns:>9} {item.ddl_partitioned:>9} "
            f"{item.with_partition_rows:>9}  {item.note}"
        )

    print()
    print("Column key:")
    print("  visible      = system.jdbc.tables rows (TABLE + VIEW)")
    print(
        "  queryable    = distinct objects in system.jdbc.columns "
        "(rows the app emits via INNER JOIN)"
    )
    print(
        "  ghosts       = visible - queryable. Expected non-zero on hive/iceberg "
        "because they share one"
    )
    print(
        "                 metastore, so each catalog lists the other's tables but "
        "cannot read columns."
    )
    print("  DDL-part     = tables whose SHOW CREATE TABLE has partitioned_by/partitioning")
    print("  has-rows     = subset of DDL-part where $partitions returns >= 1 row")
    print()
    print("Default fixture shape after setup:")
    print("- postgres keeps 20 bulk schemas, 10,000 bulk tables, 510,000 bulk columns,")
    print(
        "  plus quoted/unusual identifier objects under postgres.\"qa-with-dash\"."
    )
    print("- hive adds 4 feature schemas (default) with 6 partitioned tables + 1 view each,")
    print("  plus 3 baseline tables (page_views, clickstream, orders_snapshot).")
    print("- iceberg adds 3 feature schemas (default) with 4 partitioned tables each,")
    print("  plus baseline dim_customer.")
    print("- hive_scale and iceberg_scale alias the same metastore for many-catalog tests.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
