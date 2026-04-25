#!/usr/bin/env python3
"""
Generate additional Trino-visible Hive and Iceberg fixture SQL.

The defaults are intentionally moderate: enough objects to exercise Trino
catalog/schema/table/view/partition metadata paths without making local setup
unreasonably slow.
"""

import os


HIVE_SCHEMAS = int(os.environ.get("TRINO_HIVE_FEATURE_SCHEMAS", "4"))
HIVE_TABLES_PER_SCHEMA = int(os.environ.get("TRINO_HIVE_TABLES_PER_SCHEMA", "6"))
ICEBERG_SCHEMAS = int(os.environ.get("TRINO_ICEBERG_FEATURE_SCHEMAS", "3"))
ICEBERG_TABLES_PER_SCHEMA = int(os.environ.get("TRINO_ICEBERG_TABLES_PER_SCHEMA", "4"))

ICEBERG_PARTITIONING = [
    "day(event_ts)",
    "month(event_ts)",
    "year(event_ts)",
    "bucket(account_id, 8)",
    "truncate(region, 2)",
    "event_date",
]


def emit(line: str = "") -> None:
    print(line)


def generate_hive() -> None:
    emit("-- Generated Hive feature coverage.")
    for schema_idx in range(1, HIVE_SCHEMAS + 1):
        schema = f"feature_hive_{schema_idx:03d}"
        emit(
            f"CREATE SCHEMA IF NOT EXISTS hive.{schema} "
            f"WITH (location = 's3a://hive/{schema}/');"
        )
        emit()

        for table_idx in range(1, HIVE_TABLES_PER_SCHEMA + 1):
            table = f"partitioned_events_{table_idx:03d}"
            emit(f"CREATE TABLE IF NOT EXISTS hive.{schema}.{table} (")
            emit("    event_id    BIGINT,")
            emit("    customer_id BIGINT,")
            emit("    amount      DOUBLE,")
            emit("    event_ts    TIMESTAMP,")
            emit("    payload     VARCHAR,")
            emit("    dt          VARCHAR,")
            emit("    region      VARCHAR")
            emit(") WITH (")
            emit("    format            = 'PARQUET',")
            emit(
                f"    external_location = 's3a://hive/{schema}/{table}/',"
            )
            emit("    partitioned_by    = ARRAY['dt', 'region']")
            emit(");")
            emit()

            # Tiny seed so $partitions returns rows. 3 distinct (dt, region) combos.
            emit(f"INSERT INTO hive.{schema}.{table}")
            emit("SELECT * FROM (VALUES")
            emit(
                "    (1, 1001, 1.50, TIMESTAMP '2026-04-01 10:00:00',"
                " 'p1', '2026-04-01', 'us-east'),"
            )
            emit(
                "    (2, 1002, 2.50, TIMESTAMP '2026-04-02 11:00:00',"
                " 'p2', '2026-04-02', 'eu-west'),"
            )
            emit(
                "    (3, 1003, 3.50, TIMESTAMP '2026-04-03 12:00:00',"
                " 'p3', '2026-04-03', 'ap-south')"
            )
            emit(
                ") AS t(event_id, customer_id, amount, event_ts, payload, dt, region)"
            )
            emit(
                f"WHERE NOT EXISTS (SELECT 1 FROM hive.{schema}.{table});"
            )
            emit()

        view_table = "partitioned_events_001"
        emit(f"CREATE OR REPLACE VIEW hive.{schema}.vw_event_rollup AS")
        emit("SELECT")
        emit("    dt,")
        emit("    region,")
        emit("    count(*) AS event_count,")
        emit("    sum(amount) AS total_amount,")
        emit("    max(event_ts) AS last_event_ts")
        emit(f"FROM hive.{schema}.{view_table}")
        emit("GROUP BY dt, region;")
        emit()


def generate_iceberg() -> None:
    emit("-- Generated Iceberg feature coverage.")
    for schema_idx in range(1, ICEBERG_SCHEMAS + 1):
        schema = f"feature_iceberg_{schema_idx:03d}"
        emit(
            f"CREATE SCHEMA IF NOT EXISTS iceberg.{schema} "
            f"WITH (location = 's3a://iceberg/{schema}/');"
        )
        emit()

        for table_idx in range(1, ICEBERG_TABLES_PER_SCHEMA + 1):
            table = f"partitioned_facts_{table_idx:03d}"
            transform = ICEBERG_PARTITIONING[
                (schema_idx + table_idx - 2) % len(ICEBERG_PARTITIONING)
            ]
            emit(f"CREATE TABLE IF NOT EXISTS iceberg.{schema}.{table} (")
            emit("    event_id   BIGINT,")
            emit("    account_id BIGINT,")
            emit("    event_ts   TIMESTAMP(6),")
            emit("    event_date DATE,")
            emit("    region     VARCHAR,")
            emit("    payload    VARCHAR,")
            emit("    amount     DECIMAL(12, 2)")
            emit(") WITH (")
            emit(f"    location     = 's3a://iceberg/{schema}/{table}/',")
            emit(f"    partitioning = ARRAY['{transform}']")
            emit(");")
            emit()

            # Tiny seed spanning multiple days/months/years/account_ids/regions
            # so day/month/year/bucket/truncate/identity transforms all
            # produce non-empty $partitions.
            emit(f"INSERT INTO iceberg.{schema}.{table}")
            emit("SELECT * FROM (VALUES")
            emit(
                "    (1, 1001, TIMESTAMP '2025-01-15 10:00:00.000000',"
                " DATE '2025-01-15', 'us-east-1', 'p1', DECIMAL '1.50'),"
            )
            emit(
                "    (2, 1002, TIMESTAMP '2025-06-20 11:00:00.000000',"
                " DATE '2025-06-20', 'eu-west-1', 'p2', DECIMAL '2.50'),"
            )
            emit(
                "    (3, 1003, TIMESTAMP '2026-03-22 12:00:00.000000',"
                " DATE '2026-03-22', 'ap-south-1', 'p3', DECIMAL '3.50')"
            )
            emit(
                ") AS t(event_id, account_id, event_ts, event_date, region, payload, amount)"
            )
            emit(
                f"WHERE NOT EXISTS (SELECT 1 FROM iceberg.{schema}.{table});"
            )
            emit()


def main() -> None:
    emit("-- Generated by scripts/trino-feature-generate.py.")
    emit(
        "-- Knobs: TRINO_HIVE_FEATURE_SCHEMAS, TRINO_HIVE_TABLES_PER_SCHEMA, "
        "TRINO_ICEBERG_FEATURE_SCHEMAS, TRINO_ICEBERG_TABLES_PER_SCHEMA."
    )
    emit()
    generate_hive()
    generate_iceberg()


if __name__ == "__main__":
    main()
