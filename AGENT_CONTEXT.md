# Agent Context

## Purpose

This folder is a self-contained datasource fixture for testing Atlan Trino extraction and publish behavior. It is not an application service; it is infrastructure plus seed data that exposes multiple catalogs through Trino.

## Architecture

- `docker-compose.yml` starts Trino, PostgreSQL, MinIO, Hive Metastore, an optional basic-auth Trino instance, and an optional Cloudflare Tunnel container.
- `sql/postgres/*.sql` is mounted into the PostgreSQL container and runs during database initialization. `00-metastore-db.sql` creates the `metastore` database required by Hive Metastore.
- `scripts/setup.sh` runs after containers are up. It generates bulk Postgres tables, populates a large table, creates baseline Hive/Iceberg objects, creates configurable Trino-specific feature coverage, and generates `trino/basic-auth/password.db` (bcrypt cost 10) when `htpasswd` is available. Credentials come from `TRINO_BASIC_USER` / `TRINO_BASIC_PASSWORD` env vars with fallback defaults.
- `scripts/trino-feature-generate.py` emits generated Hive/Iceberg fixture SQL controlled by `TRINO_HIVE_FEATURE_SCHEMAS`, `TRINO_HIVE_TABLES_PER_SCHEMA`, `TRINO_ICEBERG_FEATURE_SCHEMAS`, and `TRINO_ICEBERG_TABLES_PER_SCHEMA`.
- `scripts/validate-metadata.py` queries Trino to print visible catalog, schema, table, view, column, and partitioned-table counts.
- `trino/etc/catalog/*.properties` defines the `postgres`, `hive`, and `iceberg` catalogs plus `hive_scale` and `iceberg_scale` aliases for many-catalog tests. The `postgres` catalog sets `case-insensitive-name-matching=true` so mixed-case / quoted PostgreSQL identifiers (e.g. `"qa-with-dash"."Table With Spaces"`) are queryable through Trino, not only listed in `system.jdbc.tables`.
- `trino/basic-auth` defines the auth-enabled Trino profile for tenant-facing tests. It sets `http-server.authentication.type=PASSWORD`, `http-server.process-forwarded=true` (so TLS-terminating tunnels count as HTTPS for auth), and an `internal-communication.shared-secret`.
- The `postgres` service is started with `max_locks_per_transaction=512` so the 10 000-table bulk generation fits in a single transaction.

## Main Commands

```bash
docker compose up -d
TRINO_BASIC_USER=myuser TRINO_BASIC_PASSWORD='s3cret' ./scripts/setup.sh
docker compose --profile auth-test up -d trino-basic
python3 scripts/validate-metadata.py
docker compose --profile auth-test down
```

Expose via Cloudflare Tunnel (optional):

```bash
echo 'CLOUDFLARED_TOKEN=...' > .env   # gitignored
docker compose --profile tunnel up -d cloudflared
```

To wipe generated Docker volumes:

```bash
docker compose --profile auth-test --profile tunnel down -v
```

## Test Data Shape

### High-cardinality column stress

PostgreSQL includes:

- Schemas such as `prod_sales`, `prod_marketing`, `dev_sales`, `staging`, quoted schema names, empty schemas, and internal-style schemas.
- Tables with single-column primary keys, composite primary keys, foreign keys, composite foreign keys, no-key control tables, quoted/reserved columns, comments, wide column sets, and varying row counts.
- Views and a materialized view for asset type and view SQL extraction paths.
- Generated scale data: 20 `bulk_*` schemas, 10 000 tables, ~510 000 columns.

### Trino-specific feature coverage

Hive includes:

- Partitioned tables with single and multi-column partitions.
- A non-partitioned Parquet table.
- Generated feature coverage by default: 4 `feature_hive_*` schemas, 6 partitioned tables per feature schema, and one view per feature schema. Each generated table is seeded with 3 rows spanning 3 distinct `(dt, region)` partition values so `$partitions` returns rows.

Iceberg includes:

- An Iceberg table with `month(registered_date)` partition transform.
- Generated feature coverage by default: 3 `feature_iceberg_*` schemas and 4 partitioned tables per feature schema, each seeded with 3 rows spanning multiple days/months/years/account_ids/regions so every partition transform produces non-empty `$partitions`.
- Partition transforms across generated tables: `day`, `month`, `year`, `bucket`, `truncate`, and identity partitioning where supported by Trino/Iceberg.

Catalog-scale coverage includes:

- Primary catalogs: `postgres`, `hive`, `iceberg`.
- Alias catalogs for many-catalog tests: `hive_scale`, `iceberg_scale`.
- The alias catalogs point at the same local Hive metastore, so they should be included only for catalog-scale discovery tests.

### Shared Hive/Iceberg metastore — intentional cross-catalog ghost rows

Hive and Iceberg use the same backing Hive metastore. Trino's metastore-walking code in each connector lists every entry in the metastore, including tables created by the other connector that it cannot read. The result, by design:

- `hive` and `hive_scale` each list 13 Iceberg-owned tables in `system.jdbc.tables` with no rows in `system.jdbc.columns` (1 `iceberg.curated.dim_customer` + 12 `iceberg.feature_iceberg_*.partitioned_facts_*`).
- `iceberg` and `iceberg_scale` each list 27 Hive-owned tables in `system.jdbc.tables` with no rows in `system.jdbc.columns` (24 `hive.feature_hive_*.partitioned_events_*` + 2 `hive.events_raw.*` + 1 `hive.warehouse.orders_snapshot`).
- Total: 80 cross-catalog ghost rows across the four hive/iceberg catalogs.

These ghost rows are kept on purpose — they exercise an important app behavior: any extractor that walks `system.jdbc.tables` without joining `system.jdbc.columns` will emit unqueryable assets. The Atlan Trino app uses an INNER JOIN, which correctly drops these. If a future connector or app changes that assumption, the fixture will surface the regression. To get a "clean" fixture without ghosts, give Hive and Iceberg separate metastores; the trade-off is that the app's INNER JOIN guard would no longer be exercised here.

Default expected fixture shape:

- PostgreSQL stress fixture remains 20 bulk schemas, 10 000 bulk tables, and ~510 170 bulk + core columns.
- PostgreSQL also includes quoted/unusual identifiers visible **and queryable** through Trino, including `postgres."qa-with-dash"."Table With Spaces"` and `postgres."qa-with-dash"."View With Spaces"`. The Postgres catalog config sets `case-insensitive-name-matching=true` so the columns of these objects appear in `information_schema.columns` and `system.jdbc.columns`, and `SHOW CREATE TABLE` succeeds.
- Hive directly-created objects: 27 base tables (3 baseline + 24 generated, all 26 partitioned tables seeded so `$partitions` returns rows; `orders_snapshot` is intentionally non-partitioned) and 4 generated views.
- Iceberg directly-created objects: 13 base tables (1 baseline + 12 generated, all partitioned and seeded with rows that span every partition transform).
- `scripts/validate-metadata.py` is the source of truth for actual Trino-visible counts by catalog. It distinguishes `visible` (rows in `system.jdbc.tables`), `queryable` (rows in `system.jdbc.columns`), `ghosts` (the difference, expected non-zero on hive/iceberg catalogs), `DDL-part` (tables partitioned by DDL), and `has-rows` (subset of DDL-part with non-empty `$partitions`).

Scale knobs:

```bash
TRINO_HIVE_FEATURE_SCHEMAS=8 \
TRINO_HIVE_TABLES_PER_SCHEMA=12 \
TRINO_ICEBERG_FEATURE_SCHEMAS=4 \
TRINO_ICEBERG_TABLES_PER_SCHEMA=8 \
./scripts/setup.sh
```

Validation:

```bash
python3 scripts/validate-metadata.py
TRINO_VALIDATE_CATALOGS=postgres,hive,iceberg python3 scripts/validate-metadata.py
```

## Tenant Testing Guidance

Expose only the basic-auth Trino endpoint (`trino-basic`, local port `8081`) when connecting from an Atlan tenant. Do not expose:

- no-auth Trino on `8080`
- PostgreSQL on `5432`
- MinIO on `9000` or `9001`
- Hive Metastore on `9083`

If using a tunnel, configure the tenant connection with the tunnel hostname, port `443`, HTTPS, and Basic authentication. The `cloudflared` compose service expects the tunnel token in `.env` as `CLOUDFLARED_TOKEN` and relies on the Cloudflare dashboard for hostname → `http://trino-basic:8080` routing.

## Security Notes

This repository contains local fixture credentials in config files, such as `trino/trino` and MinIO test credentials. Treat them as local-only defaults. Change the Trino basic-auth username/password before exposing the service.

Basic-auth credentials for `trino-basic` are generated from `TRINO_BASIC_USER` / `TRINO_BASIC_PASSWORD` at setup time; they are not baked into the repo. `password.db` uses bcrypt cost 10 because Trino rejects costs below 8.

The generated `trino/basic-auth/password.db` file and local `.env` (holding `CLOUDFLARED_TOKEN`) are intentionally ignored and must not be committed.
