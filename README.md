# source-provisioning-lineage

A local Trino fixture purpose-built for testing the Atlan lineage pipeline:
**`atlan-trino-app` → `atlan-query-intelligence-app` → `atlan-lineage-app` → publish.**

> **First time on this repo?** Read `CLAUDE.md` — it has the full bring-up doc,
> design rationale, asset target, and known quirks. AI agents working on this
> repo should also read `AGENTS.md` for load-bearing rules.

It seeds Trino with ~10–15k assets shaped exactly like a real production Trino source — production-grade view DDL with the same SQL constructs that stress real-world parsers (UNION ALL across 5+ sources, IF/CASE chains, `cardinality`/`bitwise_and`/`repeat`, constant columns, column aliasing) — and lets you run the full DAG end-to-end on your laptop.

This repo is the **lineage-focused** sibling of `source-provisioning`. That repo focuses on connector breadth (PostgreSQL bulk, Hive/Iceberg partitions, JWT/basic-auth) and stays around for scale and connector regression work. This repo focuses on lineage shapes.

## What's in here

| Path | Purpose |
|---|---|
| `replay/` | Anonymized DDL derived from a real production Trino run — tables + 23 views with non-trivial lineage shapes. The `views.json` and `source_tables.json` files are **safe to commit**: every catalog, schema, table, and column name has been remapped to a generic warehouse vocabulary. The mapping itself (`identifier-mapping.json`) is gitignored. |
| `synthetic/` | Optional parametric padding to bring the fixture up to the asset target and exercise SQL patterns the replay corpus doesn't cover (CTEs, window functions, view-on-view chains). |
| `scripts/extract-replay-data.py` | One-shot. Reads a golden dataset from a sibling app run, anonymizes it, writes to `replay/`. Run once when you want to refresh the fixture. |
| `scripts/seed-replay.py` | Applies replay DDL to a running Trino. |
| `scripts/seed-synthetic.py` | Applies synthetic DDL. |
| `scripts/validate-counts.py` | Queries Trino, confirms ~10–15k assets are visible. |
| `scripts/setup.sh` | Orchestrator: bring up containers, seed replay, optionally seed synthetic, validate. |
| `scripts/run-parity.sh` | Triggers the trino-app workflow against this fixture and structurally diffs against expected counts. |
| `docker-compose.yml` | Trino 448 + Hive Metastore + MinIO + PostgreSQL (metastore backend). |
| `trino/etc/` | Trino server + catalog configs. |

## Quick start

```bash
cp .env.example .env

# 1. (One-time) Build the anonymized replay assets from the trino-app golden dataset.
#    Outputs land in replay/. Safe to commit.
python3 scripts/extract-replay-data.py \
    --golden ../atlan-trino-app/new-golden-dataset \
    --out replay

# 2. Bring up the stack and seed.
./scripts/setup.sh

# 3. Verify.
python3 scripts/validate-counts.py
```

After that, point a Trino connector at `http://localhost:8080` (user `trino`, no password) and run extraction. The fixture exposes a single catalog `analytics`.

> **Why one catalog, not three?** The replayed FreeWheel data lives in one Hive metastore. Pointing multiple Trino catalogs at the same metastore would 3× the asset count (every Hive database becomes visible from every catalog). FreeWheel's production had 3 *distinct* metastores so they got 1×; locally we simplified to one catalog. To exercise multi-catalog handling, restore `events.properties` / `archive.properties` to `trino/etc/catalog/` and restart Trino.

## What lineage shapes are included

From the replay corpus (anonymized from a real production run):

- 23 views, all backed by deterministic name mappings (`vw_V0001` through `vw_V0023`)
- Source-table fan-out per view ranges from 2 to 9 source tables
- Total source-bearing column edges: 2,244 (matches the original golden dataset)
- View DDLs preserve SQL structure: `IF((flag = 'CONSTANT'), -2, value)` patterns, `repeat(-2, IF(cardinality(...) > 0, ...))`, `bitwise_and`, constant columns (`, 2 col_X`), column aliasing (`, src_col target_col`)

From synthetic (default-on, knob via env):

- CTE-based views, window function views, view-on-view chains, cross-catalog joins

## Security note

The replay corpus is derived from production Trino metadata for a real customer. **All identifiers** — catalogs, schemas, tables, columns — were renamed to opaque generic names via a deterministic mapping before being written to disk. Only SQL structure and string/numeric literals are preserved. The original mapping is never committed.

If you regenerate the replay with a fresh golden dataset, the anonymization runs automatically.

## Relationship to `source-provisioning`

| | source-provisioning | source-provisioning-lineage |
|---|---|---|
| Connector breadth | PostgreSQL + Hive + Iceberg | Hive only (sufficient for view-DDL lineage) |
| Asset count | up to 510k columns (PostgreSQL bulk) | ~10–15k targeted |
| Auth variants | basic-auth, JWT, no-auth | no-auth (sufficient for lineage testing) |
| Lineage coverage | none | 23 verbatim production view shapes + synthetic |
| Use case | connector regression, scale, partition handling | full DAG end-to-end (extract → QI → lineage-app → publish) |
