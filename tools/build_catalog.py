"""
Build the full-coverage catalog + Cortex Search service (Option A).

Creates, in DATA_ENGINEERING_HOME.CILLIAN_TEST:
  1. CORE_COLUMN_CATALOG  - one row per physical column across
     DATA_MARKETPLACE.EDW_CORE and DATA_MARKETPLACE.DATA_PRODUCT_CORE.
  2. CORE_CATALOG_SEARCH  - a Cortex Search service over that table, so the
     agent can find the right physical object/column for any legacy attribute.

Source of truth for the columns is tools/core_inventory.json (produced by
inventory_cores.py). We only load the CANONICAL schemas below, ignoring the
many _SHARE / _PROD_SHR / ESG_* copies.

Run (dry run - prints DDL only):
    .venv\\Scripts\\python.exe tools\\build_catalog.py
Run (apply):
    .venv\\Scripts\\python.exe tools\\build_catalog.py --apply
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

from snowflake.snowpark import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from service_codes import classify_service_codes  # noqa: E402
SECRETS = ROOT / ".streamlit" / "secrets.toml"
INVENTORY = Path(__file__).resolve().parent / "core_inventory.json"

# Only load the canonical source databases/schemas (ignore shares & ESG clones).
CANONICAL = {
    ("DATA_MARKETPLACE", "EDW_CORE"): "EDW",
    ("DATA_MARKETPLACE", "DATA_PRODUCT_CORE"): "DATA_PRODUCT",
}

# Medallion layer per domain: Gold = data products, Silver = curated EDW.
LAYER_BY_DOMAIN = {
    "DATA_PRODUCT": ("GOLD", 1),
    "EDW": ("SILVER", 2),
}

PROFILE = Path(__file__).resolve().parent / "core_profile.json"

TARGET_DB = "DATA_ENGINEERING_HOME"
TARGET_SCHEMA = "CILLIAN_TEST"
CATALOG = f"{TARGET_DB}.{TARGET_SCHEMA}.CORE_COLUMN_CATALOG"
SEARCH = f"{TARGET_DB}.{TARGET_SCHEMA}.CORE_CATALOG_SEARCH"
WAREHOUSE = "ENGINEERING_WH"


def load_conn() -> dict:
    with open(SECRETS, "rb") as fh:
        data = tomllib.load(fh)
    conn = dict(data["connections"]["snowflake"])
    if str(conn.get("authenticator", "")).lower() == "externalbrowser":
        conn.setdefault("client_store_temporary_credential", True)
    return conn


def build_rows() -> list[dict]:
    inv = json.loads(INVENTORY.read_text(encoding="utf-8"))
    profile = {}
    if PROFILE.exists():
        profile = json.loads(PROFILE.read_text(encoding="utf-8"))
        print(f"Merged {len(profile)} column fingerprints from {PROFILE.name}.")
    else:
        print(f"WARNING: {PROFILE.name} not found - fingerprint columns will be empty.")

    # GOLD (DATA_PRODUCT_CORE) columns carry no comments in Snowflake, so the
    # description-driven catalogue search never surfaces them and everything
    # maps to SILVER. Build a name -> description lookup from EDW (which is well
    # commented) and inherit it onto same-named GOLD columns so GOLD becomes
    # discoverable. Prefer the longest/most descriptive comment per name.
    edw_comment_by_col: dict[str, str] = {}
    for meta in inv.values():
        if (meta["database"], meta["schema"]) != ("DATA_MARKETPLACE", "EDW_CORE"):
            continue
        for c in meta["columns"]:
            cm = (c.get("comment") or "").strip()
            if not cm:
                continue
            key = c["name"].upper()
            if len(cm) > len(edw_comment_by_col.get(key, "")):
                edw_comment_by_col[key] = cm
    print(f"Built EDW description lookup for {len(edw_comment_by_col)} column names (for GOLD backfill).")

    rows: list[dict] = []
    inherited_count = 0
    for meta in inv.values():
        key = (meta["database"], meta["schema"])
        if key not in CANONICAL:
            continue
        domain = CANONICAL[key]
        layer, layer_rank = LAYER_BY_DOMAIN.get(domain, ("", 9))
        schema = meta["schema"]
        table = meta["table"]
        for c in meta["columns"]:
            col = c["name"]
            dtype = c["type"]
            comment = (c.get("comment") or "").strip()
            comment_source = "native" if comment else ""
            # Backfill GOLD descriptions from the EDW column of the same name.
            if not comment and domain == "DATA_PRODUCT":
                inherited = edw_comment_by_col.get(col.upper())
                if inherited:
                    comment = inherited
                    comment_source = "inherited_edw"
                    inherited_count += 1
            fp = profile.get(f"{schema}.{table}.{col}", {})
            sample_values = fp.get("sample_values") or []
            sample_str = ", ".join(str(v) for v in sample_values)
            pattern = fp.get("value_pattern") or ""
            service_codes = fp.get("service_codes") or []
            service_str = ", ".join(str(v) for v in service_codes)
            # Classify the source systems behind this column: strategic (ADP /
            # Fundipedia / Lipper / CRDEF) vs legacy (Phoenix). EDW columns often
            # blend both, so we keep the split and a headline classification.
            svc = classify_service_codes(service_codes)
            classification = svc["classification"]
            strategic_str = ", ".join(svc["strategic"])
            legacy_str = ", ".join(svc["legacy"])
            # Enrich the searchable blob with the value pattern + example values
            # so name-mismatched attributes can still be found by their shape.
            search_text = (
                f"{layer} {domain} {schema} {table} {col} {dtype} {comment} "
                f"pattern:{pattern} examples:{sample_str} sources:{service_str} "
                f"classification:{classification} strategic:{strategic_str} legacy:{legacy_str}".strip()
            )
            rows.append(
                {
                    "DOMAIN": domain,
                    "LAYER": layer,
                    "LAYER_RANK": layer_rank,
                    "PHYSICAL_SCHEMA": schema,
                    "PHYSICAL_TABLE": table,
                    "PHYSICAL_OBJECT": f"{schema}.{table}",
                    "COLUMN_NAME": col,
                    "DATA_TYPE": dtype,
                    "COLUMN_COMMENT": comment,
                    "COMMENT_SOURCE": comment_source,
                    "VALUE_PATTERN": pattern,
                    "DISTINCT_COUNT": fp.get("distinct_count"),
                    "NULL_RATE": fp.get("null_rate"),
                    "MIN_VALUE": (str(fp.get("min")) if fp.get("min") is not None else None),
                    "MAX_VALUE": (str(fp.get("max")) if fp.get("max") is not None else None),
                    "SAMPLE_VALUES": sample_str,
                    "SERVICE_CODES": service_str,
                    "SERVICE_CLASSIFICATION": classification,
                    "STRATEGIC_SERVICE_CODES": strategic_str,
                    "LEGACY_SERVICE_CODES": legacy_str,
                    "AS_OF_DATE": fp.get("as_of_date"),
                    "SEARCH_TEXT": search_text,
                }
            )
    print(f"Backfilled {inherited_count} GOLD column descriptions from EDW.")
    return rows


def sql_escape(v: str) -> str:
    return v.replace("'", "''")


def main() -> int:
    apply = "--apply" in sys.argv
    rows = build_rows()
    print(f"Prepared {len(rows)} catalog rows from {INVENTORY.name}.")
    if not rows:
        print("No rows - did you run inventory_cores.py first?")
        return 1

    create_tbl = (
        f"CREATE OR REPLACE TABLE {CATALOG} (\n"
        "  DOMAIN STRING,\n"
        "  LAYER STRING,\n"
        "  LAYER_RANK NUMBER,\n"
        "  PHYSICAL_SCHEMA STRING,\n"
        "  PHYSICAL_TABLE STRING,\n"
        "  PHYSICAL_OBJECT STRING,\n"
        "  COLUMN_NAME STRING,\n"
        "  DATA_TYPE STRING,\n"
        "  COLUMN_COMMENT STRING,\n"
        "  COMMENT_SOURCE STRING,\n"
        "  VALUE_PATTERN STRING,\n"
        "  DISTINCT_COUNT NUMBER,\n"
        "  NULL_RATE FLOAT,\n"
        "  MIN_VALUE STRING,\n"
        "  MAX_VALUE STRING,\n"
        "  SAMPLE_VALUES STRING,\n"
        "  SERVICE_CODES STRING,\n"
        "  SERVICE_CLASSIFICATION STRING,\n"
        "  STRATEGIC_SERVICE_CODES STRING,\n"
        "  LEGACY_SERVICE_CODES STRING,\n"
        "  AS_OF_DATE STRING,\n"
        "  SEARCH_TEXT STRING\n"
        ")"
    )
    create_svc = (
        f"CREATE OR REPLACE CORTEX SEARCH SERVICE {SEARCH}\n"
        "  ON SEARCH_TEXT\n"
        "  ATTRIBUTES DOMAIN, LAYER, LAYER_RANK, PHYSICAL_SCHEMA, PHYSICAL_TABLE, PHYSICAL_OBJECT, COLUMN_NAME, DATA_TYPE, COLUMN_COMMENT, VALUE_PATTERN, SAMPLE_VALUES, SERVICE_CODES, SERVICE_CLASSIFICATION, STRATEGIC_SERVICE_CODES, LEGACY_SERVICE_CODES\n"
        f"  WAREHOUSE = {WAREHOUSE}\n"
        "  TARGET_LAG = '1 day'\n"
        f"  AS SELECT * FROM {CATALOG}"
    )

    print("\n--- DDL ---")
    print(create_tbl)
    print(f"\nINSERT {len(rows)} rows into {CATALOG} (batched)")
    print("\n" + create_svc)

    if not apply:
        print("\nDRY RUN. Re-run with --apply to execute.")
        return 0

    conn = load_conn()
    print(f"\nConnecting as {conn.get('user')} ...")
    session = Session.builder.configs(conn).create()
    ctx = session.sql("select current_role()").collect()[0].as_dict()
    print("Connected.", ctx)

    print("Creating catalog table ...")
    session.sql(create_tbl).collect()

    # Bulk load via a pandas DataFrame -> temp stage (fast, avoids huge INSERTs).
    import pandas as pd

    df = pd.DataFrame(rows)
    session.write_pandas(
        df,
        table_name="CORE_COLUMN_CATALOG",
        database=TARGET_DB,
        schema=TARGET_SCHEMA,
        overwrite=True,
        auto_create_table=False,
        quote_identifiers=False,
    )
    cnt = session.sql(f"select count(*) as N from {CATALOG}").collect()[0].as_dict()
    print(f"Loaded rows: {cnt}")

    if "--with-search" in sys.argv:
        print("Creating Cortex Search service (needs USE AI FUNCTIONS grant) ...")
        try:
            session.sql(create_svc).collect()
            print("Search service created.")
        except Exception as exc:  # noqa: BLE001
            print(f"Search service skipped/failed: {exc}")
    else:
        print("Skipping Cortex Search service (use --with-search once the AI-function grant is in place). Using CORE_CATALOG_SV instead.")

    # Quick sanity: distinct domains / object counts.
    summary = session.sql(
        f"select DOMAIN, count(distinct PHYSICAL_OBJECT) as OBJECTS, count(*) as COLS "
        f"from {CATALOG} group by DOMAIN order by DOMAIN"
    ).collect()
    for r in summary:
        print("  ", r.as_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
