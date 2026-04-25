# trino-datasource

Local Trino datasource fixture for testing Atlan extraction and publish flows.

This stack runs:

- Trino with no auth on `localhost:8080`
- Optional Trino with basic auth on `localhost:8081`
- PostgreSQL on `localhost:5432`
- MinIO on `localhost:9000` and console on `localhost:9001`
- Hive Metastore on `localhost:9083`
- Optional Cloudflare Tunnel container for exposing `trino-basic` to the internet

## Quick Start

```bash
docker compose up -d
TRINO_BASIC_USER=myuser TRINO_BASIC_PASSWORD='s3cret' ./scripts/setup.sh
docker compose --profile auth-test up -d trino-basic
python3 scripts/validate-metadata.py
```

`TRINO_BASIC_USER` / `TRINO_BASIC_PASSWORD` default to `testuser` / `testpass` if not set.
Change them before exposing the service. `setup.sh` writes `trino/basic-auth/password.db`
with bcrypt cost 10 (Trino requires cost ≥ 8).

Use `trino-basic` for any exposed or tenant-facing test. Do not expose the no-auth Trino service.

## Atlan Connection Target

For local tenant-facing testing:

- Host: `localhost`
- Port: `8081`
- Protocol: HTTP, or HTTPS if exposed through a tunnel
- Auth: Basic
- Catalogs: `postgres`, `hive`, `iceberg`

The fixture also includes `hive_scale` and `iceberg_scale` catalog aliases that point
at the same local Hive metastore. Include them only when you want to exercise
many-catalog discovery behavior; omit them for the primary correctness crawl.

For temporary tenant testing, run this stack on a laptop or VM and expose only port `8081` through a tunnel such as Cloudflare Tunnel, ngrok, or Tailscale Funnel.

## Exposing via Cloudflare Tunnel

The compose file includes an optional `cloudflared` service under the `tunnel` profile.

1. In the Cloudflare Zero Trust dashboard, create a tunnel (connector type: Cloudflared) and copy the token.
2. Put the token in a local `.env` file (gitignored):
   ```
   CLOUDFLARED_TOKEN=eyJhIjoi...
   ```
3. In the tunnel's **Routes** tab, add a Published application:
   - Subdomain + domain of your choice (e.g. `trino.example.com`)
   - Service: `http://trino-basic:8080` (container DNS — cloudflared shares the compose network)
4. Start the tunnel:
   ```bash
   docker compose --profile tunnel up -d cloudflared
   ```
5. Verify:
   ```bash
   curl -u myuser:'s3cret' https://trino.example.com/v1/info
   ```

The `trino-basic` config sets `http-server.process-forwarded=true`, which lets Trino treat
tunnel-forwarded requests as HTTPS and permit PASSWORD auth. This is safe only when a
TLS-terminating tunnel (or proxy) is actually in front — don't expose port `8081` directly.

## Notes

The data is intentionally synthetic and designed to exercise connector behavior: keys, foreign keys, views, materialized views, comments, quoted identifiers, partitioned Hive tables, Iceberg partition transforms, wide tables, large tables, many schemas, many catalogs, and high object counts.

### High-cardinality column stress

`setup.sh` preserves the original PostgreSQL stress fixture:

- 30 Postgres schemas, 10 000 Postgres tables, 510 000 Postgres columns
- 20 generated `bulk_*` schemas
- 500 generated tables per bulk schema
- 51 columns per generated table
- Existing PostgreSQL core tables, comments, keys, views, quoted identifiers, and the 1 M-row `prod_sales.large` table

### Trino-specific feature coverage

By default, `setup.sh` also creates moderate Trino-native coverage:

- Hive baseline: partitioned `page_views`, partitioned `clickstream`, and non-partitioned `orders_snapshot`
- Hive feature scale: 4 `feature_hive_*` schemas x 6 partitioned tables each, plus one view per feature schema
- Iceberg baseline: `iceberg.curated.dim_customer` partitioned by `month(registered_date)`
- Iceberg feature scale: 3 `feature_iceberg_*` schemas x 4 partitioned tables each
- Iceberg partition transforms across generated tables: `day`, `month`, `year`, `bucket`, `truncate`, and identity partitioning
- Quoted/unusual PostgreSQL identifiers visible through Trino, including `postgres."qa-with-dash"."Table With Spaces"`
- Catalog-scale aliases: `hive_scale` and `iceberg_scale`

Scale knobs:

```bash
TRINO_HIVE_FEATURE_SCHEMAS=8 \
TRINO_HIVE_TABLES_PER_SCHEMA=12 \
TRINO_ICEBERG_FEATURE_SCHEMAS=4 \
TRINO_ICEBERG_TABLES_PER_SCHEMA=8 \
./scripts/setup.sh
```

### Metadata validation

Run validation through Trino, not PostgreSQL:

```bash
python3 scripts/validate-metadata.py
```

Useful overrides:

```bash
TRINO_VALIDATE_CATALOGS=postgres,hive,iceberg python3 scripts/validate-metadata.py
```

The script prints Trino-visible counts by catalog: schemas, tables, views, columns,
and partitioned tables. Because Hive and Iceberg share the local Hive metastore,
schema lists may overlap between those catalogs; treat the script output as the
source of truth for what the Atlan Trino app will see.
