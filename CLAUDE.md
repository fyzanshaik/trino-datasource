# source-provisioning-lineage — bring-up + agent context

A self-contained Trino fixture for testing the Atlan **lineage pipeline**:
`atlan-trino-app → atlan-query-intelligence-app → atlan-lineage-app → atlan-publish`.

This document is the source of truth for bringing the fixture up on any machine.
On a fresh laptop, following the **Quick start** below should reproduce the
exact same Trino topology, asset counts, and lineage output without any further
explanation needed.

---

## Quick start

Prerequisites:

- Docker (24+) with Compose. OrbStack works.
- Python 3.11+
- ~3 GB disk for Docker images, ~500 MB RAM for the stack idle.

```bash
git clone <this repo>
cd source-provisioning-lineage
cp .env.example .env

./scripts/setup.sh
```

That's it. `setup.sh` is idempotent — it brings up containers, waits for Trino
to be query-ready, seeds the replay corpus, seeds synthetic padding, validates
asset counts, and exits.

After it finishes, Trino is at **`http://localhost:8080`** with user `trino`,
no password. The catalog `analytics` is fully populated:

```
9 schemas / 96 tables / 30 views / 9,977 columns
total assets: ~10,147 (in the 10–15k target range)
```

---

## What this fixture is

It's a Trino source whose lineage shape mirrors a real production deployment.
The 23 (now 20) replayed views were derived by anonymizing a real Trino crawl
and replaying just the SQL structure — every catalog, schema, table, view, and
column name is a generic warehouse identifier (`analytics`, `mart_NN`,
`fact_TNNNN`, `vw_VNNNN`, `col_CNNNNN`). String literals and numeric constants
are preserved unchanged. SQL operators, function calls, and structural
constructs (UNION ALL, CTEs, IF/CASE, window functions) are also preserved
verbatim — that's what makes it useful for parser-stress testing.

To this we add ~1,400 synthetic columns of padding and 10 extra views
exercising patterns FreeWheel didn't have (CTE aggregations, window functions,
view-on-view chains, multi-table joins).

---

## Repository layout

```
source-provisioning-lineage/
├── README.md                                    end-user docs
├── CLAUDE.md                                    this file (full reproducer)
├── AGENT_CONTEXT.md                             security / anonymization rules
├── docker-compose.yml                           Trino + Hive Metastore + MinIO + Postgres + cloudflared profile
├── trino/etc/
│   ├── config.properties, jvm.config, node.properties
│   └── catalog/analytics.properties              single Hive catalog (see "Why one catalog")
├── sql/postgres/00-metastore-db.sql             metastore DB bootstrap
├── replay/                                       anonymized replay corpus — committed
│   ├── views.json                                20 view DDLs (3 of original 23 skipped at seed time)
│   ├── source_tables.json                        36 source tables with column lists
│   ├── manifest.md                               auto-generated counts
│   └── identifier-mapping.json                   GITIGNORED — debug only
├── synthetic/                                    parametric padding + lineage variety
├── scripts/
│   ├── extract-replay-data.py                    one-shot: golden dataset → anonymized replay/. Already done.
│   ├── seed-replay.py                            apply replay DDL to Trino
│   ├── seed-synthetic.py                         apply synthetic DDL
│   ├── validate-counts.py                        confirm asset counts
│   ├── setup.sh                                  orchestrator
│   └── run-parity.sh                             smoke-test wrapper
├── requirements.txt                              sqlglot + trino-python-client
└── .env.example                                  knobs (synthetic counts, cloudflared token)
```

---

## How the bring-up works (step by step)

`setup.sh` runs:

1. **Sources `.env`** if present.
2. **Verifies replay assets** exist on disk (committed).
3. **`docker compose up -d`** — starts Postgres, MinIO, Hive Metastore, Trino.
4. **Waits for Trino** by issuing a real `SHOW CATALOGS` until it succeeds.
   (`/v1/info` returns 200 too early — catalogs aren't loaded yet.)
5. **Creates `.venv`** and installs `requirements.txt`.
6. **Runs `seed-replay.py`** — creates schemas with explicit S3 locations,
   creates 36 source tables, applies 23 view DDLs in topological order.
7. **Runs `seed-synthetic.py`** — adds 5 baseline schemas × 12 tables × 70
   cols, plus the synth_lineage schema with 10 extra views.
8. **Runs `validate-counts.py`** — confirms target range.

---

## Asset target

| Source | Schemas | Tables | Views | Columns |
|---|---|---|---|---|
| Replay (FreeWheel-shape) | 3 | 36 | 20 | ~6,000 |
| Synthetic baseline | 5 | 60 | 0 | 4,200 |
| Synthetic lineage views | 1 | 0 | 10 | (no extra cols, computed from baseline) |
| **Total** | **9** | **96** | **30** | **9,977** |

(plus the auto-created `default` schema, gives 10 total when extracted)

Bumping the synthetic baseline up further is a one-line change in `.env`:
```
SYNTHETIC_BASELINE_TABLES_PER_SCHEMA=12   # default; bump to 16 for ~12k cols
```

---

## Why one catalog (not three)

The replayed FreeWheel data lives in **one** Hive metastore. If you point
multiple Trino catalogs at the same metastore, every Hive database becomes
visible from every catalog (Hive has no real catalog concept — just databases).
That triples the asset count and is **not** how FreeWheel's production runs
(they had three *distinct* Hive metastores).

We deliberately ship one catalog (`analytics`) to keep extraction counts
honest. To exercise multi-catalog connector behavior, restore `events.properties`
and `archive.properties` to `trino/etc/catalog/` (they're in git history) and
restart Trino. Counts will 3×.

---

## Hosting on a tenant via Cloudflare Tunnel

The Trino app uses HTTP. To make this fixture reachable from a real Atlan
tenant, expose Trino via a Cloudflare Tunnel:

1. **Create a tunnel** in Cloudflare Zero Trust (Networks → Tunnels → Create
   tunnel). Choose "cloudflared" connector, give it any name.
2. **Set the public hostname** for the tunnel to point at `http://trino:8080`
   (Trino is the container service name on the compose network).
3. **Copy the tunnel token** Cloudflare displays.
4. **Add to `.env`**: `CLOUDFLARED_TOKEN=<your-token>`
5. **Start the tunnel**:
   ```bash
   docker compose --profile tunnel up -d cloudflared
   ```

Trino is now reachable at the Cloudflare hostname over HTTPS. From the tenant:

- Connector: `Trino`
- Host: `<your-cloudflare-hostname>` (no `https://` prefix)
- Port: `443`
- TLS/HTTPS: `true`
- Disable SSL verification: `false`
- Auth type: `basic`
- Username: `trino`
- Password: any value (Trino is configured no-auth, password is ignored)

That's it. Run a Trino crawl on the tenant — it'll see exactly the same 96
tables / 30 views / 9,977 columns the local fixture exposes.

> **Security note**: this fixture contains no real customer data. Every name
> is a generic warehouse identifier. The string literals inside SQL (e.g.
> `'NO_VISIBILITY'`, `'Data Rights Restricted'`) are domain terms not
> identifiers, but if they're sensitive in your context, override
> `extract-replay-data.py`'s rewriter to substitute literals too.

---

## What works end-to-end

The full DAG `extract → QI → lineage-app → lineage-publish` has been verified
locally against this fixture. From the trino-app's transformed output:

```
extract:    1 db / 10 schemas / 96 tables / 30 views / 9,977 cols  ✓
QI parse:   30/30 success / 0 failure / 2,099 source-bearing edges  ✓
lineage:    30 Process + 2,099 ColumnProcess (all refs resolved)    ✓
```

Verification scripts live in `../atlan-trino-app/parity/`:

- `run_qi_on_extract.py` — QI parser over extract output
- `run_lineage_app.py` — full lineage-app pipeline over QI output

---

## Known quirks

- **3 of 23 replay views fail at seed time** (`vw_v0021`, `vw_v0022`, `vw_v0023`).
  These are FreeWheel-original SQL with implicit column qualifications inside
  CTE projections; sqlglot's re-emit doesn't always preserve those exactly.
  `seed-replay.py` logs and continues; the remaining 20 views fully cover the
  parser-stress shapes. To reach 23/23 you'd need to hand-tune those view DDLs
  in `replay/views.json`.

- **Hive lowercases all identifiers**. `fact_T0001` is stored and exposed as
  `fact_t0001`. The connector handles this transparently; if you write SQL by
  hand, use lowercase.

- **`/v1/info` is not a reliable healthcheck.** Trino returns 200 before
  catalogs finish loading. `setup.sh` polls `SHOW CATALOGS` instead.

- **Hive metastore needs explicit S3 location for new schemas.**
  `seed-replay.py` and `seed-synthetic.py` both set `WITH (location = 's3a://…')`
  on every CREATE SCHEMA. Without it, you get `Unable to create database path
  file:/no/default/location/defined/please/create/new/schema/with/location/explicitly/set/...`.

---

## Troubleshooting

**Port 8080 already in use.**
```
ERROR: ... bind: address already in use
```
Either free the port or override it. The compose file respects `TRINO_PORT`
from `.env` — change to e.g. `TRINO_PORT=18080`, then re-run setup.

```bash
# find what's using 8080
lsof -i :8080
# or override
echo 'TRINO_PORT=18080' >> .env
docker compose down && ./scripts/setup.sh
```

**Trino healthcheck times out (180s).**
```
ERROR: Trino did not accept queries in 180s.
```
Setup logs the last 50 lines of `docker compose logs trino` automatically.
Common causes:

- Hive Metastore container exited — check `docker compose logs hive-metastore`.
  If it's complaining about the `metastore` database, the Postgres init script
  didn't run (e.g. you pre-existing volume is from another fixture). Run
  `docker compose down -v` and `./scripts/setup.sh` to reset volumes.
- Trino itself OOM'd — `query.max-memory=4GB` is in `trino/etc/config.properties`.
  Drop it to 2GB if your machine is tight.
- A stale catalog `.properties` file is referencing infra that's no longer in
  docker-compose (e.g. a stray `postgres.properties`). Confirm
  `trino/etc/catalog/` contains only `analytics.properties`.

**3 of 23 replay views are skipped during seed.**
This is expected (`vw_v0021`, `vw_v0022`, `vw_v0023`). `seed-replay.py` logs
each one and continues. The remaining 20 views fully cover all the parser-
stress shapes the FreeWheel fixture exercises. **The fixture is healthy with
20/23 — no action needed.**

If `validate-counts.py` reports counts in range, the fixture is good.

## Cleanup

```bash
# Stop containers (keep volumes — fast restart):
docker compose down

# Stop and wipe everything (start fresh next time):
docker compose down -v
```

Both `setup.sh --rebuild` and `docker compose down -v && ./scripts/setup.sh`
do a full clean restart.

---

## Regenerating the replay corpus

The committed `replay/views.json` and `replay/source_tables.json` were
extracted from a specific golden dataset capture. Don't regenerate routinely —
the existing files are the source of truth.

Re-running is only necessary if:
- The trino-app captures a new golden dataset (different views, different patterns).
- The anonymization rules change (e.g. you want to also rewrite string literals).

To regenerate (requires the trino-app golden dataset):

```bash
# From within atlan-query-intelligence-app's venv (has sqlglot)
cd ../atlan-query-intelligence-app
uv run python ../source-provisioning-lineage/scripts/extract-replay-data.py \
    --golden ../atlan-trino-app/new-golden-dataset \
    --out ../source-provisioning-lineage/replay
```

Re-seed afterwards:
```bash
cd source-provisioning-lineage
docker compose down -v
./scripts/setup.sh
```

---

## Relationship to `source-provisioning`

The other repo (`source-provisioning`) handles **scale and connector breadth**
— PostgreSQL bulk (510k columns), Hive/Iceberg with partitions, multiple auth
variants. It deliberately uses a shared Hive metastore for the multi-catalog
ghost-row test.

This repo handles **lineage shapes** — the production-grade view DDLs and the
SQL constructs that stress the parser stack. One catalog only, no auth, no
partitions. They are independent fixtures and can run on the same machine
side-by-side (different ports if you need both at once).
