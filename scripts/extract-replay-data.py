#!/usr/bin/env python3
"""Build anonymized replay assets from a Trino app golden dataset.

Reads:
  {golden}/expected-output/view-db-0.json   transformed View entities (with full DDL)
  {golden}/extract/columns-db.json          raw JDBC column metadata
  {golden}/extract/table-db.json            raw JDBC table metadata

Writes:
  {out}/views.json                  anonymized view DDLs (safe to commit)
  {out}/source_tables.json          anonymized source-table column lists (safe to commit)
  {out}/identifier-mapping.json     old↔new mapping (gitignored — debug only)
  {out}/manifest.md                 human-readable summary

Anonymization rules:
  - All identifiers (catalog/schema/table/view/column names) are replaced
    via a deterministic mapping. Sort inputs alphabetically, assign sequential
    opaque names. Re-running on the same input produces the same output.
  - SQL operators, function names, string literals, and numeric literals are
    preserved unchanged.
  - Column-name mapping is global (not per-table). Two tables that share a
    column name rename to the same generic name. Required for UNION views.
  - View names get a separate prefix (`vw_V*`) so they're distinguishable
    from tables (`fact_T*`) in the seeded fixture.

Usage:
    python3 scripts/extract-replay-data.py \\
        --golden ../atlan-trino-app/new-golden-dataset \\
        --out replay
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

try:
    import sqlglot
    from sqlglot import expressions as exp
except ImportError:
    sys.stderr.write(
        "sqlglot is required. Install with: pip install -r requirements.txt\n"
    )
    sys.exit(2)


# ── Anonymization vocabulary ──────────────────────────────────────────

# Catalogs are hardcoded — there are only a few in any FreeWheel-shaped source.
# Map original FreeWheel catalog names to generic warehouse names. If the
# input contains a catalog not in this map, an opaque `cat_<n>` is assigned.
CATALOG_MAP = {
    "db": "analytics",
    "fw": "events",
    "mrm_log_flat": "archive",
}


def opaque_schema(idx: int) -> str:
    return f"mart_{idx + 1:02d}"


def opaque_table(idx: int) -> str:
    return f"fact_T{idx + 1:04d}"


def opaque_view(idx: int) -> str:
    return f"vw_V{idx + 1:04d}"


def opaque_column(idx: int) -> str:
    return f"col_C{idx + 1:05d}"


def opaque_alias(idx: int) -> str:
    return f"a{idx + 1:04d}"


# ── Loader: read raw golden dataset ───────────────────────────────────


def load_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def load_views(golden: Path) -> list[dict]:
    """Returns transformed View entities — has full DDL + qualifiedName."""
    return load_jsonl(golden / "expected-output" / "view-db-0.json")


def load_columns(golden: Path) -> list[dict]:
    """Returns raw JDBC column metadata for the 'db' catalog."""
    return load_jsonl(golden / "extract" / "columns-db.json")


# ── Identifier discovery ──────────────────────────────────────────────


def collect_view_table_refs(views: list[dict]) -> dict[tuple[str, str, str], set[str]]:
    """For each view, find every (catalog, schema, table) it references.

    Normalizes implicitly-qualified tables (no catalog) by inheriting the
    home catalog of the view. Same for missing schema.

    Returns: {(catalog, schema, table) -> {referencing view qualifiedName, ...}}
    """
    out: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for v in views:
        attrs = v["attributes"]
        view_qn = attrs["qualifiedName"]
        view_catalog = attrs["databaseName"]
        view_schema = attrs["schemaName"]
        sql = attrs["definition"]

        ast = sqlglot.parse_one(sql, dialect="trino")
        for t in ast.find_all(exp.Table):
            # Skip the CREATE VIEW target itself
            cat = t.catalog or view_catalog
            db = t.db or view_schema
            name = t.name
            if name == attrs["name"] and cat == view_catalog and db == view_schema:
                continue
            out[(cat, db, name)].add(view_qn)
    return out


def collect_view_columns(views: list[dict]) -> set[str]:
    """All column names appearing in any view DDL (target columns + source refs)."""
    cols: set[str] = set()
    for v in views:
        sql = v["attributes"]["definition"]
        ast = sqlglot.parse_one(sql, dialect="trino")
        for c in ast.find_all(exp.Column):
            if c.name:
                cols.add(c.name)
        # Also pick up output column aliases in the SELECT list
        for alias in ast.find_all(exp.Alias):
            if alias.alias:
                cols.add(alias.alias)
    return cols


def collect_aliases(views: list[dict]) -> set[str]:
    """Collect every table/subquery/CTE alias used inside view DDLs.

    These are short mnemonics chosen by the original SQL author (e.g. `rate`
    for a rate-card table) and DO leak intent if not renamed. We give each a
    deterministic opaque name (`a0001`, `a0002`, …).

    We exclude anything that's already a known catalog/schema/table/view/column
    name (those are renamed via the main mapping).
    """
    aliases: set[str] = set()
    for v in views:
        sql = v["attributes"]["definition"]
        ast = sqlglot.parse_one(sql, dialect="trino")

        # Table aliases inside FROM / JOIN clauses
        for t in ast.find_all(exp.Table):
            if t.alias:
                aliases.add(t.alias)

        # Subquery aliases: (SELECT …) AS x
        for sq in ast.find_all(exp.Subquery):
            if sq.alias:
                aliases.add(sq.alias)

        # CTE names: WITH x AS (…)
        for cte in ast.find_all(exp.CTE):
            if cte.alias:
                aliases.add(cte.alias)

        # Qualified column refs: x.col_y → `x` is a table alias
        for c in ast.find_all(exp.Column):
            if c.table:
                aliases.add(c.table)
    return aliases


def collect_source_table_columns(
    columns: list[dict],
    refs: Iterable[tuple[str, str, str]],
) -> dict[tuple[str, str, str], list[dict]]:
    """For each referenced source table, return its column list from extract.

    Source data is JDBC-shaped: each column dict has TABLE_SCHEM, TABLE_NAME,
    COLUMN_NAME, TYPE_NAME, ORDINAL_POSITION, IS_NULLABLE.
    """
    refs_set = set(refs)
    out: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for c in columns:
        cat = c["TABLE_CAT"]
        sch = c["TABLE_SCHEM"]
        tbl = c["TABLE_NAME"]
        if (cat, sch, tbl) in refs_set:
            out[(cat, sch, tbl)].append(c)
    # Sort each table's columns by ordinal position
    for k in out:
        out[k].sort(key=lambda d: d["ORDINAL_POSITION"])
    return out


# ── Mapping construction ──────────────────────────────────────────────


def build_mapping(
    views: list[dict],
    table_refs: dict[tuple[str, str, str], set[str]],
    source_columns: dict[tuple[str, str, str], list[dict]],
    extra_columns: set[str],
    aliases: set[str],
) -> dict[str, Any]:
    """Build deterministic old→new mapping for catalog/schema/table/view/column.

    Sorting is the load-bearing piece — assigns names by sorted order so
    re-running produces the same mapping.
    """
    # Catalogs
    catalogs = sorted({c for (c, _, _) in table_refs} |
                       {v["attributes"]["databaseName"] for v in views})
    catalog_map: dict[str, str] = {}
    fallback_idx = 0
    for c in catalogs:
        if c in CATALOG_MAP:
            catalog_map[c] = CATALOG_MAP[c]
        else:
            catalog_map[c] = f"cat_{fallback_idx:02d}"
            fallback_idx += 1

    # Schemas (global — schema names like 'aggregate' map to one opaque value
    # regardless of catalog)
    schemas = sorted({s for (_, s, _) in table_refs} |
                      {v["attributes"]["schemaName"] for v in views})
    schema_map = {old: opaque_schema(i) for i, old in enumerate(schemas)}

    # Tables
    table_names = sorted({t for (_, _, t) in table_refs})
    table_map = {old: opaque_table(i) for i, old in enumerate(table_names)}

    # Views
    view_names = sorted({v["attributes"]["name"] for v in views})
    view_map = {old: opaque_view(i) for i, old in enumerate(view_names)}

    # Columns: union of source-table cols and view-DDL cols
    all_cols: set[str] = set(extra_columns)
    for cols in source_columns.values():
        for c in cols:
            all_cols.add(c["COLUMN_NAME"])
    column_names = sorted(all_cols)
    column_map = {old: opaque_column(i) for i, old in enumerate(column_names)}

    # Aliases — exclude only aliases that ARE actually catalog/schema/table/view
    # names (those use the relevant rename via rename_alias_token's fallback).
    # Aliases that coincide with COLUMN names still need their own opaque rename:
    # they appear in different syntactic positions (Table.alias, TableAlias,
    # Column.table) and the column map doesn't apply there.
    unknown_aliases = {
        a for a in aliases
        if a not in catalog_map
        and a not in schema_map
        and a not in table_map
        and a not in view_map
    }
    alias_names = sorted(unknown_aliases)
    alias_map = {old: opaque_alias(i) for i, old in enumerate(alias_names)}

    return {
        "catalog": catalog_map,
        "schema": schema_map,
        "table": table_map,
        "view": view_map,
        "column": column_map,
        "alias": alias_map,
    }


# ── SQL rewriter ──────────────────────────────────────────────────────


def rewrite_sql(
    sql: str,
    home_catalog: str,
    home_schema: str,
    mapping: dict[str, Any],
    table_or_view: str,
) -> tuple[str, str, str, str]:
    """Rewrite SQL: rename every identifier per `mapping`, regenerate Trino SQL.

    Returns (rewritten_sql, new_catalog, new_schema, new_view_name).
    """
    catalog_m = mapping["catalog"]
    schema_m = mapping["schema"]
    table_m = mapping["table"]
    view_m = mapping["view"]
    column_m = mapping["column"]
    alias_m = mapping.get("alias", {})

    def rename_alias_token(name: str | None) -> str | None:
        """Map a free-floating identifier token (table alias, CTE name, etc.)."""
        if not name:
            return name
        if name in alias_m:
            return alias_m[name]
        if name in table_m:
            return table_m[name]
        if name in view_m:
            return view_m[name]
        return name

    ast = sqlglot.parse_one(sql, dialect="trino")

    # Identify the CREATE VIEW target so we rename it correctly (view → view_m,
    # not table_m)
    target_node = None
    if isinstance(ast, exp.Create) and ast.this and isinstance(ast.this, exp.Schema):
        # CREATE VIEW … : ast.this is a Schema wrapping the view's identifier
        # In sqlglot, Create.this can be a Table (for views/tables alike)
        pass
    # Find the table identifier of the CREATE VIEW
    if isinstance(ast, exp.Create):
        for t in ast.find_all(exp.Table):
            if t.name == table_or_view:
                target_node = t
                break

    def _rename_table(node: exp.Expression) -> exp.Expression:
        if not isinstance(node, exp.Table):
            return node
        old_cat = node.catalog or home_catalog
        old_sch = node.db or home_schema
        old_name = node.name
        new_cat = catalog_m.get(old_cat, old_cat)
        new_sch = schema_m.get(old_sch, old_sch)
        # Is this the view target, a known table, or a known view?
        if node is target_node:
            new_name = view_m.get(old_name, old_name)
        elif old_name in view_m:
            new_name = view_m[old_name]
        elif old_name in table_m:
            new_name = table_m[old_name]
        else:
            # Unknown reference (e.g. a CTE or temp) — leave as-is
            return node
        # Rebuild — preserve original quoting style (default unquoted)
        rebuilt = exp.Table(
            this=exp.to_identifier(new_name),
            db=exp.to_identifier(new_sch) if new_sch else None,
            catalog=exp.to_identifier(new_cat) if new_cat else None,
        )
        # Preserve and rename alias if present
        if node.alias:
            new_alias = rename_alias_token(node.alias)
            rebuilt = exp.alias_(rebuilt, new_alias, table=True)
        return rebuilt

    def _rename_column(node: exp.Expression) -> exp.Expression:
        if not isinstance(node, exp.Column):
            return node
        new_name = column_m.get(node.name, node.name)
        new_table = rename_alias_token(node.table) if node.table else None
        new_db = schema_m.get(node.db, node.db) if node.db else None
        new_cat = catalog_m.get(node.catalog, node.catalog) if node.catalog else None
        if new_name == node.name and new_table == node.table and new_db == node.db and new_cat == node.catalog:
            return node
        return exp.column(
            new_name,
            table=new_table,
            db=new_db,
            catalog=new_cat,
        )

    def _rename_alias(node: exp.Expression) -> exp.Expression:
        # Output-column aliases (`, expr AS alias_name`) and subquery /
        # CTE aliases (`(SELECT …) AS alias`, `WITH alias AS (…)`).
        if isinstance(node, exp.Alias) and node.alias:
            old = node.alias
            new = column_m.get(old) or alias_m.get(old) or table_m.get(old) or view_m.get(old)
            if new and new != old:
                # Preserve "table" alias semantics when applicable
                is_table_alias = isinstance(node.this, (exp.Table, exp.Subquery))
                return exp.alias_(node.this, new, table=is_table_alias)
        return node

    def _rename_table_alias_node(node: exp.Expression) -> exp.Expression:
        # Catches stand-alone TableAlias nodes (CTE names live here)
        if isinstance(node, exp.TableAlias):
            old = node.name
            new = rename_alias_token(old)
            if new and new != old:
                return exp.TableAlias(this=exp.to_identifier(new))
        return node

    # Replace tenant-specific UDFs with built-ins that preserve column-level
    # dependencies. The vanilla Trino we use locally doesn't have these UDFs
    # registered; replacing with a CASE that touches every original argument
    # keeps lineage edges intact while letting Trino validate the view DDL.
    CUSTOM_UDFS = {"utc_to_networklocal", "networklocal_to_utc"}

    def _rewrite_custom_udfs(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Anonymous) and node.name.lower() in CUSTOM_UDFS:
            args = list(node.expressions)
            if len(args) >= 2:
                # IF((arg2 IS NOT NULL OR arg2 IS NULL), arg1, arg1)
                # — both branches yield arg1 (the timestamp), but arg2 is
                # referenced in the predicate, preserving lineage on it.
                cond = exp.Or(
                    this=exp.Is(this=args[1].copy(), expression=exp.Not(this=exp.Null())),
                    expression=exp.Is(this=args[1].copy(), expression=exp.Null()),
                )
                return exp.If(this=cond, true=args[0].copy(), false=args[0].copy())
            if len(args) == 1:
                return args[0]
        return node

    # Apply transforms
    ast = ast.transform(_rename_table)
    ast = ast.transform(_rename_column)
    ast = ast.transform(_rename_alias)
    ast = ast.transform(_rename_table_alias_node)
    # Custom UDF rewrites can be nested (e.g. outer wraps date_trunc(inner_udf(…))).
    # Iterate to fixed point — a few passes are enough for any practical depth.
    for _ in range(8):
        before = ast.sql(dialect="trino")
        ast = ast.transform(_rewrite_custom_udfs)
        if ast.sql(dialect="trino") == before:
            break

    rewritten = ast.sql(dialect="trino", pretty=False)

    new_cat = catalog_m.get(home_catalog, home_catalog)
    new_sch = schema_m.get(home_schema, home_schema)
    new_view_name = view_m.get(table_or_view, table_or_view)
    return rewritten, new_cat, new_sch, new_view_name


# ── Type translation ──────────────────────────────────────────────────


# We emit Trino-native types for CREATE TABLE (the Hive connector accepts
# Trino types and translates internally). For lineage purposes, the semantic
# that matters is the column NAME, not the type — but we still want the table
# to actually be creatable, so unknown / overly-rich types degrade to varchar.
TRINO_TYPE_BASE = {
    "tinyint": "tinyint",
    "smallint": "smallint",
    "integer": "integer",
    "int": "integer",
    "bigint": "bigint",
    "real": "real",
    "double": "double",
    "boolean": "boolean",
    "varbinary": "varbinary",
    "date": "date",
    "json": "varchar",       # Trino has json but Hive can't store it; varchar is safe
    "uuid": "varchar",
    "ipaddress": "varchar",
}


def translate_type(trino_type: str) -> str:
    """Map a Trino type from JDBC metadata to a Trino type valid in CREATE TABLE.

    Targets the Hive connector — which accepts Trino types and translates
    internally to Hive's underlying storage types.
    """
    t = trino_type.strip().lower()
    base = t.split("(")[0]
    # Direct map
    if base in TRINO_TYPE_BASE:
        return TRINO_TYPE_BASE[base]
    # varchar(n), char(n) — drop the length to keep it simple
    if base in ("varchar", "char"):
        return "varchar"
    # decimal(p,s) — keep precision/scale
    if base == "decimal":
        return t
    # timestamp[(p)] [with time zone] — drop TZ; Hive doesn't have it
    if base.startswith("timestamp"):
        return "timestamp"
    # array<…> / array(…) — preserve element type for simple cases. View DDLs
    # use IF/CASE expressions where both branches must agree on element type,
    # so we preserve the inner type when it's a known scalar. Nested arrays
    # and row() elements degrade to array(varchar) for safety.
    if base.startswith("array"):
        # Extract inner type
        inner = t[len("array"):].strip()
        if inner.startswith("(") and inner.endswith(")"):
            inner_t = inner[1:-1].strip()
        elif inner.startswith("<") and inner.endswith(">"):
            inner_t = inner[1:-1].strip()
        else:
            inner_t = ""
        # Recurse for simple inner types only
        inner_base = inner_t.split("(")[0].split("<")[0]
        SIMPLE_INNER = {"tinyint", "smallint", "integer", "int", "bigint",
                        "real", "double", "boolean", "varchar", "char",
                        "date"}
        if inner_base in SIMPLE_INNER:
            return f"array({translate_type(inner_t)})"
        # decimal(p,s) inside array
        if inner_base == "decimal":
            return f"array({inner_t})"
        # Anything richer (array(array(...)), array(row(...))) → flatten
        return "array(varchar)"
    # map<…> / map(…)
    if base.startswith("map"):
        return "map(varchar, varchar)"
    # row(…) — varchar is safest cross-connector default
    if base.startswith("row"):
        return "varchar"
    # Unknown — degrade to varchar
    return "varchar"


# ── Output writers ────────────────────────────────────────────────────


def write_views_json(
    views: list[dict],
    mapping: dict[str, Any],
    out_path: Path,
) -> list[dict]:
    """Rewrite each view's DDL with anonymized identifiers, write to disk."""
    out: list[dict] = []
    for v in views:
        attrs = v["attributes"]
        rewritten_sql, new_cat, new_sch, new_name = rewrite_sql(
            attrs["definition"],
            attrs["databaseName"],
            attrs["schemaName"],
            mapping,
            attrs["name"],
        )
        out.append({
            "catalog": new_cat,
            "schema": new_sch,
            "name": new_name,
            "definition": rewritten_sql,
            "column_count": attrs.get("columnCount"),
        })
    out.sort(key=lambda d: (d["catalog"], d["schema"], d["name"]))
    with out_path.open("w") as f:
        for r in out:
            f.write(json.dumps(r) + "\n")
    return out


def write_source_tables_json(
    source_columns: dict[tuple[str, str, str], list[dict]],
    mapping: dict[str, Any],
    out_path: Path,
) -> list[dict]:
    """For each referenced source table, write anonymized column list."""
    catalog_m = mapping["catalog"]
    schema_m = mapping["schema"]
    table_m = mapping["table"]
    column_m = mapping["column"]
    out: list[dict] = []
    for (cat, sch, tbl), cols in source_columns.items():
        new_cat = catalog_m.get(cat, cat)
        new_sch = schema_m.get(sch, sch)
        new_tbl = table_m.get(tbl, tbl)
        anon_cols = []
        for c in cols:
            old_col = c["COLUMN_NAME"]
            new_col = column_m.get(old_col, old_col)
            anon_cols.append({
                "name": new_col,
                "type": translate_type(c["TYPE_NAME"]),
                "ordinal": c["ORDINAL_POSITION"],
                "nullable": c.get("IS_NULLABLE", "YES") == "YES",
            })
        anon_cols.sort(key=lambda d: d["ordinal"])
        out.append({
            "catalog": new_cat,
            "schema": new_sch,
            "name": new_tbl,
            "columns": anon_cols,
        })
    out.sort(key=lambda d: (d["catalog"], d["schema"], d["name"]))
    with out_path.open("w") as f:
        for r in out:
            f.write(json.dumps(r) + "\n")
    return out


def write_mapping_json(mapping: dict[str, Any], out_path: Path) -> None:
    """Write the old↔new mapping. This file is gitignored."""
    with out_path.open("w") as f:
        json.dump(mapping, f, indent=2, sort_keys=True)


def write_manifest(
    views_out: list[dict],
    tables_out: list[dict],
    mapping: dict[str, Any],
    out_path: Path,
) -> None:
    n_views = len(views_out)
    n_tables = len(tables_out)
    n_columns = sum(len(t["columns"]) for t in tables_out)
    n_view_columns = sum(v.get("column_count") or 0 for v in views_out)
    catalogs = sorted({t["catalog"] for t in tables_out} | {v["catalog"] for v in views_out})
    schemas = sorted({t["schema"] for t in tables_out} | {v["schema"] for v in views_out})
    body = f"""# Replay corpus manifest

Generated by `scripts/extract-replay-data.py`. The mapping that ties these
anonymized names back to the original source is gitignored.

## Counts

| | count |
|---|---|
| views                    | {n_views} |
| source tables            | {n_tables} |
| source-table columns     | {n_columns} |
| view output columns      | {n_view_columns} |
| catalogs                 | {len(catalogs)} |
| schemas                  | {len(schemas)} |

## Catalogs

{chr(10).join(f"- `{c}`" for c in catalogs)}

## Schemas

{chr(10).join(f"- `{s}`" for s in schemas)}

## Views (anonymized)

{chr(10).join(f"- `{v['catalog']}.{v['schema']}.{v['name']}` (~{v.get('column_count') or 0} cols)" for v in views_out)}
"""
    out_path.write_text(body)


# ── Main ──────────────────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--golden", required=True, type=Path,
                   help="Path to the trino-app golden-dataset directory")
    p.add_argument("--out", required=True, type=Path,
                   help="Output directory (replay/)")
    args = p.parse_args()

    golden: Path = args.golden
    out: Path = args.out

    if not golden.exists():
        print(f"ERROR: golden dataset not found: {golden}", file=sys.stderr)
        return 2

    out.mkdir(parents=True, exist_ok=True)

    print(f"reading {golden}")
    views = load_views(golden)
    columns = load_columns(golden)
    print(f"  views:           {len(views)}")
    print(f"  source columns:  {len(columns)}")

    table_refs = collect_view_table_refs(views)
    print(f"  unique table refs across views: {len(table_refs)}")

    source_columns = collect_source_table_columns(columns, table_refs.keys())
    missing = set(table_refs) - set(source_columns)
    if missing:
        print(f"  WARN: {len(missing)} referenced tables have no column metadata:")
        for ref in sorted(missing)[:5]:
            print(f"    {ref}")
        # These are typically self-references or tables in catalogs not in the
        # golden dataset. We still want to map them so the SQL stays valid; we
        # just won't have column data to seed them.

    extra_cols = collect_view_columns(views)
    print(f"  unique column names across view DDLs: {len(extra_cols)}")

    aliases = collect_aliases(views)
    print(f"  unique aliases across view DDLs:      {len(aliases)}")

    print("building mapping")
    mapping = build_mapping(views, table_refs, source_columns, extra_cols, aliases)
    print(f"  catalogs: {len(mapping['catalog'])}")
    print(f"  schemas:  {len(mapping['schema'])}")
    print(f"  tables:   {len(mapping['table'])}")
    print(f"  views:    {len(mapping['view'])}")
    print(f"  columns:  {len(mapping['column'])}")
    print(f"  aliases:  {len(mapping['alias'])}")

    print("rewriting SQL + writing outputs")
    views_path = out / "views.json"
    tables_path = out / "source_tables.json"
    mapping_path = out / "identifier-mapping.json"
    manifest_path = out / "manifest.md"

    views_out = write_views_json(views, mapping, views_path)
    tables_out = write_source_tables_json(source_columns, mapping, tables_path)
    write_mapping_json(mapping, mapping_path)
    write_manifest(views_out, tables_out, mapping, manifest_path)

    print(f"\nWrote:")
    print(f"  {views_path}      ({len(views_out)} views, {views_path.stat().st_size:,} bytes)")
    print(f"  {tables_path}     ({len(tables_out)} tables, {tables_path.stat().st_size:,} bytes)")
    print(f"  {mapping_path}    (gitignored — debug only)")
    print(f"  {manifest_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
