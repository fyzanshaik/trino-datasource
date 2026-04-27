# AGENTS.md — guidance for AI agents working on this repo

## Read this first

This is a **test fixture**, not a production service. Its job is to seed a
local Trino instance with production-shaped data so the Atlan lineage pipeline
(`atlan-trino-app → atlan-query-intelligence-app → atlan-lineage-app`) can be
exercised end-to-end without a tenant.

The full bring-up doc, asset target, design rationale, and known quirks live
in `CLAUDE.md`. Read that before making any change.

## Load-bearing rules (do not break)

1. **Never commit `replay/identifier-mapping.json`.** It is gitignored. The
   anonymized DDL files (`views.json`, `source_tables.json`) are safe because
   without the mapping there's no way to correlate them back to the original
   tenant. Committing the mapping defeats the anonymization.

2. **Never re-add original FreeWheel identifiers.** Any time you touch
   `extract-replay-data.py`, run a leak check before committing — see the
   verification at the end of `CLAUDE.md`. Original catalog/schema/table/view/
   column/alias names must not appear anywhere in `replay/views.json` or
   `replay/source_tables.json`. SQL operators, function names, and string/
   numeric literals are NOT identifiers and are preserved verbatim.

3. **One catalog by default.** Don't re-add `events.properties` or
   `archive.properties` to `trino/etc/catalog/` without a clear reason — it
   3×s the asset count because all three Trino catalogs would alias the same
   Hive metastore. The README and CLAUDE.md both explain why.

4. **Schemas need explicit S3 locations.** Hive metastore won't auto-pick a
   default. Every `CREATE SCHEMA` in `seed-replay.py` and `seed-synthetic.py`
   must include `WITH (location = 's3a://<catalog>/<schema>/')`. This is a
   common surprise — keep the pattern.

5. **Trino types in CREATE TABLE, not Hive types.** The Hive connector accepts
   Trino-native types (`varchar`, `array(integer)`, `decimal(p,s)`) and
   translates internally. Don't emit Hive-native names like `string` or
   `array<int>` — Trino rejects them.

## Coding conventions

- **Python 3.11+**. Type hints required. `from __future__ import annotations`
  at the top of every script.
- **Scripts are executable** (`chmod +x`) and have shebangs.
- **No frameworks.** This is plain Python + Docker Compose. Keep it that way.
- **Idempotency.** Every seed step uses `CREATE … IF NOT EXISTS` or drop-then-
  create. Re-running `setup.sh` should converge to the same state.
- **Determinism.** Anonymization mappings are sorted-then-numbered so re-running
  `extract-replay-data.py` against the same input produces identical output.

## Testing changes

When you modify the seed scripts or the rewriter:

```bash
# 1. Rebuild from scratch
docker compose down -v && ./scripts/setup.sh

# 2. Confirm asset count parity
.venv/bin/python scripts/validate-counts.py

# 3. (Optional) Re-run end-to-end through QI + lineage-app
#    See ../atlan-trino-app/parity/run_qi_on_extract.py
#    and ../atlan-trino-app/parity/run_lineage_app.py
```

Any change that affects the replay corpus also requires running
`extract-replay-data.py` against a golden dataset and verifying:
- Zero FreeWheel identifier leaks (regex word-boundary scan).
- All 23 view DDLs re-parse cleanly via sqlglot Trino dialect.
- Source-bearing column edges remain at 2,244 (proves lineage shape preserved).

## When in doubt

Compare with `../source-provisioning/` — the older sibling fixture. Many
patterns (compose layout, Hive Metastore service definition, MinIO bucket
setup, cloudflared tunnel profile) are deliberately mirrored.
