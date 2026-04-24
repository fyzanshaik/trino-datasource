# trino-datasource

Local Trino datasource fixture for testing Atlan extraction and publish flows.

This stack runs:

- Trino with no auth on `localhost:8080`
- Optional Trino with basic auth on `localhost:8081`
- PostgreSQL on `localhost:5432`
- MinIO on `localhost:9000` and console on `localhost:9001`
- Hive Metastore on `localhost:9083`

## Quick Start

```bash
docker compose up -d
./scripts/setup.sh
docker compose --profile auth-test up -d trino-basic
```

Use `trino-basic` for any exposed or tenant-facing test. Do not expose the no-auth Trino service.

## Atlan Connection Target

For local testing:

- Host: `localhost`
- Port: `8081`
- Protocol: HTTP, or HTTPS if exposed through a tunnel
- Auth: Basic
- Catalogs: `postgres`, `hive`, `iceberg`

For temporary tenant testing, run this stack on a laptop or VM and expose only port `8081` through a tunnel such as Cloudflare Tunnel, ngrok, or Tailscale Funnel.

## Notes

The data is intentionally synthetic and designed to exercise connector behavior: keys, foreign keys, views, materialized views, comments, quoted identifiers, partitioned Hive tables, Iceberg partition transforms, wide tables, large tables, and high object counts.
