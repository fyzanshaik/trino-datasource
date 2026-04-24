# Agent Context

## Purpose

This folder is a self-contained datasource fixture for testing Atlan Trino extraction and publish behavior. It is not an application service; it is infrastructure plus seed data that exposes multiple catalogs through Trino.

## Architecture

- `docker-compose.yml` starts Trino, PostgreSQL, MinIO, Hive Metastore, and an optional basic-auth Trino instance.
- `sql/postgres/*.sql` is mounted into the PostgreSQL container and runs during database initialization.
- `scripts/setup.sh` runs after containers are up. It generates bulk Postgres tables, populates a large table, creates Hive objects, creates Iceberg objects, and generates `trino/basic-auth/password.db` when `htpasswd` is available.
- `trino/etc/catalog/*.properties` defines the `postgres`, `hive`, and `iceberg` catalogs.
- `trino/basic-auth` defines the auth-enabled Trino profile for tenant-facing tests.

## Main Commands

```bash
docker compose up -d
./scripts/setup.sh
docker compose --profile auth-test up -d trino-basic
docker compose --profile auth-test down
```

To wipe generated Docker volumes:

```bash
docker compose --profile auth-test down -v
```

## Test Data Shape

PostgreSQL includes:

- Schemas such as `prod_sales`, `prod_marketing`, `dev_sales`, `staging`, quoted schema names, empty schemas, and internal-style schemas.
- Tables with single-column primary keys, composite primary keys, foreign keys, composite foreign keys, no-key control tables, quoted/reserved columns, comments, wide column sets, and varying row counts.
- Views and a materialized view for asset type and view SQL extraction paths.
- Optional generated scale data: 20 `bulk_*` schemas, 10,000 tables, and about 510,000 columns.

Hive includes:

- Partitioned tables with single and multi-column partitions.
- A non-partitioned Parquet table.

Iceberg includes:

- An Iceberg table with `month(registered_date)` partition transform.

## Tenant Testing Guidance

Expose only the basic-auth Trino endpoint (`trino-basic`, local port `8081`) when connecting from an Atlan tenant. Do not expose:

- no-auth Trino on `8080`
- PostgreSQL on `5432`
- MinIO on `9000` or `9001`
- Hive Metastore on `9083`

If using a tunnel, configure the tenant connection with the tunnel hostname, port `443`, HTTPS, and Basic authentication.

## Security Notes

This repository contains local fixture credentials in config files, such as `trino/trino` and MinIO test credentials. Treat them as local-only defaults. Change the Trino basic-auth username/password before exposing the service.

The generated `trino/basic-auth/password.db` file is intentionally ignored and should not be committed.
