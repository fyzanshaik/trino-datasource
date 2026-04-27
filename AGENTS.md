# AGENTS.md — guidance for AI agents working on this repo

## Read this first

This is a **test fixture**, not a production service. Its job is to seed a
local Trino instance with production-shaped data so the Atlan lineage pipeline
(`atlan-trino-app → atlan-query-intelligence-app → atlan-lineage-app`) can be
exercised end-to-end without a tenant.

The full bring-up doc, asset target, design rationale, and known quirks live
in `CLAUDE.md`. Read that before making any change.

## Bring-up checklist for an autonomous agent

If a user hands you this repo and says "set it up" or "bring it up", do the
following without asking. The goal is a fully operational fixture (Trino
seeded + Cloudflare tunnel live if a token is present) in one shot.

### Required actions, in order

1. **Verify prerequisites are present.** `docker info` succeeds. `python3 --version` is 3.11 or newer. Stop and report if either fails.

2. **Ensure `.env` exists.** If the user has pre-populated `.env` (e.g. it already has `CLOUDFLARED_TOKEN`), leave it alone. If `.env` is missing, `cp .env.example .env`. Never overwrite an existing `.env`.

3. **Run `./scripts/setup.sh`.** That script does everything:
   - Brings up Trino, Hive Metastore, MinIO, Postgres.
   - **Automatically enables the Cloudflare tunnel profile if `CLOUDFLARED_TOKEN` is set** in `.env`. No flag needed.
   - Polls Trino until `SHOW CATALOGS` succeeds (catalogs take ~60–120s after the HTTP info endpoint says 200).
   - Creates `.venv`, installs `requirements.txt`.
   - Seeds the replay corpus (20 views + 36 source tables; 3 of 23 views fail by design — see "Expected failures" below).
   - Seeds synthetic padding (60 tables + 10 views) unless `ENABLE_SYNTHETIC=false`.
   - Validates asset counts against the 10–15k target.
   - If the tunnel is enabled, polls cloudflared for "Registered tunnel connection" before declaring success.

4. **Confirm validation passes.** `validate-counts.py` should print `✓ … is within target range 10000–15000`. The total should be ~10,147 with default knobs.

5. **If the tunnel was started, confirm it's live.** Look for `Registered tunnel connection` in `docker compose logs cloudflared`. If you see "no auth token", the token is malformed or missing — re-check `.env`.

6. **Report back to the user.** Include: total asset count, which 3 views were skipped (expected), tunnel status (registered or not), public hostname (the user knows it from their Cloudflare config; you can grep for it in cloudflared logs if it's logged).

### Do NOT do these things

- **Do not run `extract-replay-data.py`** unless the user explicitly asks for replay regeneration. The committed `replay/views.json` and `replay/source_tables.json` are the source of truth; regenerating requires the trino-app golden dataset which lives in a sibling repo and may not be present.
- **Do not edit `replay/views.json` or `replay/source_tables.json` by hand.** They were anonymized programmatically — any direct edit risks reintroducing identifiers from the original source.
- **Do not commit `replay/identifier-mapping.json`.** Gitignored. If you find it staged, unstage it.
- **Do not commit `.env`** — it contains `CLOUDFLARED_TOKEN` which is a secret.
- **Do not change the catalog topology.** Single catalog (`analytics`) is the deliberate default. Adding `events`/`archive` `.properties` files 3×s the asset count because they would alias the same Hive metastore. CLAUDE.md → "Why one catalog" explains why.
- **Do not silently accept more than 3 view-creation failures.** 3 of 23 are documented to fail (`vw_v0021`, `vw_v0022`, `vw_v0023`) — see "Expected failures" below. If the count grows, that is a regression in the type translator or sqlglot rewriter; investigate before continuing.
- **Do not force-push or amend published commits.**
- **Do not skip the validate-counts step** — it's the only end-to-end signal that the seed worked.

### Expected failures (these are not bugs)

- **3 of 23 replay views fail at seed time.** `seed-replay.py` logs each one and continues. The remaining 20 views still cover every parser-stress shape we care about. `validate-counts.py` reports 30 total views (20 replay + 10 synthetic) — that's the target.
- **`SERVER_STARTING_UP` from Trino in the first ~60s.** Expected. `setup.sh` polls past it.
- **MinIO buckets named `events` and `archive` are created** even though those catalogs aren't used. Harmless — pre-provisioned for the case someone re-enables them.

### When something goes wrong

`CLAUDE.md` → Troubleshooting covers:
- Port 8080 already in use → override `TRINO_PORT` in `.env`.
- Trino healthcheck times out → `docker compose logs hive-metastore` first; usually a stale volume.
- The 3-of-23 view-skip message — yes, expected.

For anything else, read the actual error from `docker compose logs <service>`
and address the root cause. Don't paper over with sleeps or retries.

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
