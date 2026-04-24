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
```

`TRINO_BASIC_USER` / `TRINO_BASIC_PASSWORD` default to `testuser` / `testpass` if not set.
Change them before exposing the service. `setup.sh` writes `trino/basic-auth/password.db`
with bcrypt cost 10 (Trino requires cost ≥ 8).

Use `trino-basic` for any exposed or tenant-facing test. Do not expose the no-auth Trino service.

## Atlan Connection Target

For local testing:

- Host: `localhost`
- Port: `8081`
- Protocol: HTTP, or HTTPS if exposed through a tunnel
- Auth: Basic
- Catalogs: `postgres`, `hive`, `iceberg`

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

The data is intentionally synthetic and designed to exercise connector behavior: keys, foreign keys, views, materialized views, comments, quoted identifiers, partitioned Hive tables, Iceberg partition transforms, wide tables, large tables, and high object counts.

`setup.sh` plus `bulk-generate.sql` produces approximately:

- 30 Postgres schemas, 10 000 Postgres tables, 510 000 Postgres columns
- A handful of Hive partitioned/non-partitioned tables
- One Iceberg table with a `month()` partition transform
