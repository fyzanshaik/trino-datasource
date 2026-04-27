# Agent context — source-provisioning-lineage

## What this is

A self-contained Trino fixture for **lineage-pipeline testing**. Not an Atlan app. Not a service. A Docker-Compose stack + DDL generators that recreate a production-shaped Trino source on localhost so the full `extract → QI → lineage-app → publish` DAG can be exercised end-to-end.

It is **not** a replacement for `source-provisioning` — that repo handles scale and connector regression. This repo focuses on lineage shapes and exists in parallel.

## Where the assets come from

The 23 views + their upstream tables in `replay/` are derived from a single production Trino run captured as a golden dataset under `atlan-trino-app/new-golden-dataset/`. The capture step:

1. Reads `new-golden-dataset/extract/{view-db,table-db,columns-db}.json` (raw JDBC fetch output) and `new-golden-dataset/expected-output/view-db-0.json` (transformed views with full DDL).
2. Walks each view DDL with sqlglot's Trino dialect, collects every `(catalog, schema, table)` reference.
3. Builds a deterministic identifier mapping: every original catalog/schema/table/column name gets a generic warehouse name (`analytics`, `mart_NN`, `fact_T0001`, `col_C00001`, `vw_V0001`).
4. Rewrites both the source-table column lists and the view DDLs via sqlglot AST transform — replacing identifier nodes only, preserving SQL structure, literals, and operators.
5. Writes `replay/views.json`, `replay/source_tables.json` (safe to commit) and `replay/identifier-mapping.json` (gitignored, debug-only).

After that the fixture is self-contained — the original golden dataset is no longer needed at runtime.

## Anonymization rules (load-bearing)

- **What is renamed**: catalog names, schema names, table names, view names, column names. Including all references inside `CREATE VIEW … AS SELECT … FROM …`.
- **What is NOT renamed**: SQL operators, function names, string literals (including business terms inside string equality checks), numeric literals, whitespace.
- **Determinism**: The mapping is reproducible. Sort identifiers by name, assign sequential opaque IDs. Re-running `extract-replay-data.py` against the same input produces the same output.
- **Independence**: Column-name mapping is global, not per-table. Two source tables that both contain column `network_id` rename to the same generic name. This is required for `UNION` views where both sides reference the same column.

The mapping itself is the only artifact that ties anonymized names back to the original source. It is gitignored. Committing it would defeat the anonymization.

## Trino topology

**Single catalog by default** — `analytics`, backed by a Hive metastore.

The fixture originally shipped with 3 catalogs (analytics / events / archive) but they all pointed at the same Hive metastore, which causes 3× asset multiplication on extraction (every Hive database is visible from every Trino catalog that connects to the metastore — Hive has no real catalog concept, just databases). FreeWheel's production setup had 3 *distinct* metastores, so they got 1×; we can't replicate that locally without 3 separate Hive instances, so we simplified to one catalog. This gives clean 1× extraction numbers that match `validate-counts.py`.

To exercise multi-catalog connector behavior, drop `events.properties` / `archive.properties` back into `trino/etc/catalog/` and restart Trino — both files are kept in `git history` for reference.

## Asset target

| Source | Tables | Views | Columns | Total assets |
|---|---|---|---|---|
| Replay | ~80 | 23 | ~10,000 | ~10,100 |
| Synthetic baseline | 30–50 | 0 | ~3,000 | ~3,030 |
| Synthetic lineage | 10–20 | 10–15 | ~1,500 | ~1,525 |
| **Total** | **~120–150** | **~33–38** | **~14,500** | **~14,700** |

Tunable via env knobs in `.env`. Default config lands in the 10–15k range.

## What this does NOT do

- Provision Atlan credentials or create connections (do that via `atlan-trino-app`'s dev-flow).
- Trigger workflows on a real tenant (only against local apps via `run-parity.sh`).
- Run any of the SDK apps. Those are sibling repos.
- Generate query history (no `mine` workflow data — only view-DDL lineage).

## Key constraint

**Never commit `replay/identifier-mapping.json`.** It is gitignored. The anonymized DDL files (`views.json`, `source_tables.json`) are safe because the mapping is what would let an attacker correlate names back to the original tenant.
