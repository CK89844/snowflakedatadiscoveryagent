"""
Read-only inventory of the source cores we want the agent to cover.

Lists databases/schemas/tables/columns for:
  - EDW_CORE     (e.g. DATA_MARKETPLACE.EDW_CORE)
  - DATA_PRODUCT_CORE (locate wherever it lives)

Writes a machine-readable JSON snapshot to tools/core_inventory.json so the
semantic-view generator can consume it without re-querying.

Run:
    .venv\\Scripts\\python.exe tools\\inventory_cores.py
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from snowflake.snowpark import Session

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / ".streamlit" / "secrets.toml"
OUT = Path(__file__).resolve().parent / "core_inventory.json"

# Schemas we want to cover. We search for these schema names across databases
# the role can see, so we do not hard-code the wrong database.
TARGET_SCHEMAS = ["EDW_CORE", "DATA_PRODUCT_CORE"]


def load_conn() -> dict:
    with open(SECRETS, "rb") as fh:
        data = tomllib.load(fh)
    conn = dict(data["connections"]["snowflake"])
    if str(conn.get("authenticator", "")).lower() == "externalbrowser":
        conn.setdefault("client_store_temporary_credential", True)
    return conn


def rows(session: Session, sql: str) -> list[dict]:
    try:
        return [r.as_dict() for r in session.sql(sql).collect()]
    except Exception as exc:  # noqa: BLE001
        print(f"  ! query failed: {sql}\n    {exc}")
        return []


def main() -> int:
    conn = load_conn()
    print(f"Connecting as {conn.get('user')} ...")
    session = Session.builder.configs(conn).create()
    ctx = session.sql("select current_account(), current_role()").collect()[0].as_dict()
    print("Connected.", ctx)

    # 1. Find every (database, schema) whose schema name matches a target.
    print("\nLocating target schemas across visible databases ...")
    schema_hits: list[tuple[str, str]] = []
    for tgt in TARGET_SCHEMAS:
        found = rows(session, f"show schemas like '{tgt}' in account")
        for r in found:
            db = r.get("database_name") or r.get("database")
            nm = r.get("name")
            if db and nm:
                schema_hits.append((db, nm))
                print(f"  found {db}.{nm}")
    if not schema_hits:
        print("  (none found via 'in account' — falling back to INFORMATION_SCHEMA scan)")

    # 2. Pull columns for tables/views in each located schema via INFORMATION_SCHEMA.
    inventory: dict[str, dict] = {}
    seen_dbs = sorted({db for db, _ in schema_hits})
    for db in seen_dbs:
        wanted = [s for d, s in schema_hits if d == db]
        in_list = ", ".join(f"'{s}'" for s in wanted)
        sql = (
            f"select table_schema, table_name, column_name, data_type, "
            f"ordinal_position, comment "
            f"from {db}.INFORMATION_SCHEMA.COLUMNS "
            f"where table_schema in ({in_list}) "
            f"order by table_schema, table_name, ordinal_position"
        )
        cols = rows(session, sql)
        for c in cols:
            key = f"{db}.{c['TABLE_SCHEMA']}.{c['TABLE_NAME']}"
            entry = inventory.setdefault(
                key,
                {
                    "database": db,
                    "schema": c["TABLE_SCHEMA"],
                    "table": c["TABLE_NAME"],
                    "columns": [],
                },
            )
            entry["columns"].append(
                {
                    "name": c["COLUMN_NAME"],
                    "type": c["DATA_TYPE"],
                    "position": c["ORDINAL_POSITION"],
                    "comment": c.get("COMMENT"),
                }
            )

    OUT.write_text(json.dumps(inventory, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {len(inventory)} objects -> {OUT}")
    for key, meta in inventory.items():
        print(f"  {key}  ({len(meta['columns'])} cols)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
