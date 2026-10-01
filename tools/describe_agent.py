"""
Read-only inspection of the Cortex Agent.
Reuses the same secrets/connection the Streamlit app uses.
Run:  .venv\\Scripts\\python.exe tools\\describe_agent.py
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

from snowflake.snowpark import Session

SECRETS = Path(__file__).resolve().parents[1] / ".streamlit" / "secrets.toml"


def load_cfg() -> tuple[dict, dict]:
    with open(SECRETS, "rb") as fh:
        data = tomllib.load(fh)
    conn = dict(data["connections"]["snowflake"])
    agent = dict(data.get("agent", {}))
    if str(conn.get("authenticator", "")).lower() == "externalbrowser":
        conn.setdefault("client_store_temporary_credential", True)
    return conn, agent


def run(session: Session, sql: str) -> None:
    print("\n" + "=" * 78)
    print("SQL:", sql)
    print("-" * 78)
    try:
        rows = session.sql(sql).collect()
        if not rows:
            print("(no rows)")
        for r in rows:
            print(r.as_dict())
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")


def main() -> int:
    conn, agent = load_cfg()
    db = agent.get("database", "DATA_ENGINEERING_HOME")
    schema = agent.get("schema", "CILLIAN_TEST")
    name = agent.get("name", "DATA_DISCOVERY_AGENT")
    fq = f"{db}.{schema}.{name}"

    print(f"Connecting to Snowflake as {conn.get('user')} …")
    session = Session.builder.configs(conn).create()
    print("Connected. Current context:")
    run(session, "select current_account(), current_role(), current_warehouse()")

    # Discover the agent + its definition.
    run(session, f"show agents like '{name}' in schema {db}.{schema}")
    run(session, f"describe agent {fq}")

    # Semantic views visible in the schema (candidate tools for the agent).
    run(session, f"show semantic views in schema {db}.{schema}")
    run(session, f"show semantic views in database {db}")

    session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
