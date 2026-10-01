"""
Path 2: create a Cortex Analyst semantic view over the column catalog.

This lets the agent query CORE_COLUMN_CATALOG (all 7,810 physical columns in
EDW_CORE + DATA_PRODUCT_CORE) via a cortex_analyst_text_to_sql tool, without
needing the AI-function grant that Cortex Search requires.

Creates DATA_ENGINEERING_HOME.CILLIAN_TEST.CORE_CATALOG_SV.

Run (dry run):
    .venv\\Scripts\\python.exe tools\\build_catalog_semantic_view.py
Run (apply):
    .venv\\Scripts\\python.exe tools\\build_catalog_semantic_view.py --apply
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

from snowflake.snowpark import Session

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / ".streamlit" / "secrets.toml"

TARGET_DB = "DATA_ENGINEERING_HOME"
TARGET_SCHEMA = "CILLIAN_TEST"
CATALOG = f"{TARGET_DB}.{TARGET_SCHEMA}.CORE_COLUMN_CATALOG"
SV = f"{TARGET_DB}.{TARGET_SCHEMA}.CORE_CATALOG_SV"

# A Cortex Analyst semantic view. One logical table over the catalog, with the
# physical-object columns exposed as dimensions plus rich synonyms so the model
# maps business language to the right physical EDW_CORE / DATA_PRODUCT_CORE object.
DDL = f"""
CREATE OR REPLACE SEMANTIC VIEW {SV}
  TABLES (
    catalog AS {CATALOG}
      PRIMARY KEY (PHYSICAL_OBJECT, COLUMN_NAME)
      COMMENT = 'Catalog of every physical column in EDW_CORE (Silver) and DATA_PRODUCT_CORE (Gold), with value fingerprints.'
  )
  DIMENSIONS (
    catalog.domain AS domain
      WITH SYNONYMS = ('layer', 'source layer', 'edw or data product')
      COMMENT = 'Which strategic layer the object lives in: EDW or DATA_PRODUCT.',
    catalog.layer AS layer
      WITH SYNONYMS = ('medallion', 'gold silver bronze', 'tier')
      COMMENT = 'Medallion tier: GOLD (DATA_PRODUCT_CORE) or SILVER (EDW_CORE). Prefer GOLD.',
    catalog.layer_rank AS layer_rank
      WITH SYNONYMS = ('priority', 'preference rank')
      COMMENT = 'Search priority: 1 = GOLD, 2 = SILVER. Lower is preferred.',
    catalog.physical_schema AS physical_schema
      WITH SYNONYMS = ('schema', 'database schema')
      COMMENT = 'Physical schema name (EDW_CORE or DATA_PRODUCT_CORE).',
    catalog.physical_table AS physical_table
      WITH SYNONYMS = ('table', 'dataset', 'entity')
      COMMENT = 'Physical table name.',
    catalog.physical_object AS physical_object
      WITH SYNONYMS = ('object', 'schema table', 'fully qualified table')
      COMMENT = 'Physical object as SCHEMA.TABLE - always report mappings using this.',
    catalog.column_name AS column_name
      WITH SYNONYMS = ('field', 'attribute', 'column')
      COMMENT = 'Physical column name within the object.',
    catalog.data_type AS data_type
      WITH SYNONYMS = ('type', 'datatype')
      COMMENT = 'Column data type.',
    catalog.column_comment AS column_comment
      WITH SYNONYMS = ('description', 'definition', 'business meaning')
      COMMENT = 'Business description of the column, when available.',
    catalog.value_pattern AS value_pattern
      WITH SYNONYMS = ('format', 'shape', 'signature', 'regex', 'looks like')
      COMMENT = 'Detected value shape (ISIN, LEI, SEDOL, CUSIP, ISO_COUNTRY, ISO_CCY, DATE, INTEGER, DECIMAL, CODE, TEXT...). Match legacy values to this when names differ.',
    catalog.sample_values AS sample_values
      WITH SYNONYMS = ('example values', 'examples', 'distinct values', 'domain values')
      COMMENT = 'Comma-separated example distinct values for low-cardinality coded columns. Compare legacy sample values against these.',
    catalog.service_codes AS service_codes
      WITH SYNONYMS = ('source systems', 'sources', 'service codes', 'feeds', 'service_cd')
      COMMENT = 'Source systems (SERVICE_CD values) present in the table; each was profiled separately so values cover all sources.',
    catalog.comment_source AS comment_source
      WITH SYNONYMS = ('description source', 'is description inherited')
      COMMENT = 'Where the description came from: native (on the column) or inherited_edw (borrowed from the same-named EDW column for GOLD).',
    catalog.min_value AS min_value
      COMMENT = 'Minimum observed value (latest snapshot).',
    catalog.max_value AS max_value
      COMMENT = 'Maximum observed value (latest snapshot).',
    catalog.as_of_date AS as_of_date
      WITH SYNONYMS = ('snapshot date', 'profiled date')
      COMMENT = 'Snapshot date the profile was taken from.'
  )
  METRICS (
    catalog.column_count AS COUNT(catalog.column_name)
      COMMENT = 'Number of columns matching the filter.',
    catalog.distinct_count AS MAX(catalog.distinct_count)
      COMMENT = 'Approximate distinct value count for the column.',
    catalog.null_rate AS MAX(catalog.null_rate)
      COMMENT = 'Fraction of rows where the column is null (0-1).'
  )
  COMMENT = 'Full-coverage catalog of EDW_CORE (Silver) and DATA_PRODUCT_CORE (Gold) columns, with value fingerprints, for attribute discovery, value-based matching and legacy-report mapping.'
"""


def load_conn() -> dict:
    with open(SECRETS, "rb") as fh:
        data = tomllib.load(fh)
    conn = dict(data["connections"]["snowflake"])
    if str(conn.get("authenticator", "")).lower() == "externalbrowser":
        conn.setdefault("client_store_temporary_credential", True)
    return conn


def main() -> int:
    apply = "--apply" in sys.argv
    print("DDL to run:\n" + DDL)
    if not apply:
        print("\nDRY RUN. Re-run with --apply to execute.")
        return 0

    conn = load_conn()
    print(f"Connecting as {conn.get('user')} ...")
    session = Session.builder.configs(conn).create()
    print("Connected.", session.sql("select current_role()").collect()[0].as_dict())

    try:
        session.sql(DDL).collect()
        print(f"Created semantic view {SV}.")
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR creating semantic view: {exc}")
        return 1

    rows = session.sql(f"show semantic views like 'CORE_CATALOG_SV' in schema {TARGET_DB}.{TARGET_SCHEMA}").collect()
    for r in rows:
        print("Confirmed:", r.as_dict().get("name"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
