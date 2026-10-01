"""
Apply an updated Cortex Agent specification.

Reuses the same secrets/connection the Streamlit app uses. The new spec adds
Search + Lineage behaviour on top of the existing legacy-report Mapping, and
forces a single machine-parseable JSON envelope so the app never has to guess
at loose text.

Steps:
  1. Reads tools/agent_spec.json.
  2. Backs up the CURRENT live spec to tools/agent_spec.backup.json.
  3. Runs CREATE OR REPLACE AGENT ... FROM SPECIFICATION $$...$$.

Run (dry run, prints SQL only):
    .venv\\Scripts\\python.exe tools\\update_agent.py
Run (actually apply):
    .venv\\Scripts\\python.exe tools\\update_agent.py --apply
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

from snowflake.snowpark import Session

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / ".streamlit" / "secrets.toml"
SPEC_PATH = Path(__file__).resolve().parent / "agent_spec.json"
BACKUP_PATH = Path(__file__).resolve().parent / "agent_spec.backup.json"


def load_cfg() -> tuple[dict, dict]:
    with open(SECRETS, "rb") as fh:
        data = tomllib.load(fh)
    conn = dict(data["connections"]["snowflake"])
    agent = dict(data.get("agent", {}))
    if str(conn.get("authenticator", "")).lower() == "externalbrowser":
        conn.setdefault("client_store_temporary_credential", True)
    return conn, agent


def main() -> int:
    apply = "--apply" in sys.argv
    conn, agent = load_cfg()
    db = agent.get("database", "DATA_ENGINEERING_HOME")
    schema = agent.get("schema", "CILLIAN_TEST")
    name = agent.get("name", "DATA_DISCOVERY_AGENT")
    fq = f"{db}.{schema}.{name}"

    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    spec_json = json.dumps(spec, indent=2)

    print(f"Connecting to Snowflake as {conn.get('user')} …")
    session = Session.builder.configs(conn).create()
    ctx = session.sql(
        "select current_account(), current_role(), current_warehouse()"
    ).collect()[0].as_dict()
    print("Connected. Context:", ctx)

    # Back up the current live spec before we touch anything.
    try:
        rows = session.sql(f"describe agent {fq}").collect()
        if rows:
            current = rows[0].as_dict()
            live_spec = current.get("agent_spec")
            if live_spec:
                BACKUP_PATH.write_text(live_spec, encoding="utf-8")
                print(f"Backed up current live spec -> {BACKUP_PATH}")
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: could not back up current spec: {exc}")

    ddl = (
        f"CREATE OR REPLACE AGENT {fq}\n"
        "WITH PROFILE='{\"display_name\":\"Data Discovery Agent\"}'\n"
        "COMMENT='Data marketplace agent: search, legacy-report mapping and lineage. Returns a single JSON envelope.'\n"
        f"FROM SPECIFICATION $$\n{spec_json}\n$$"
    )

    print("\n" + "=" * 78)
    print("DDL to run:")
    print("-" * 78)
    print(ddl)
    print("=" * 78)

    if not apply:
        print("\nDRY RUN. Re-run with --apply to execute.")
        return 0

    try:
        session.sql(ddl).collect()
        print("\nAgent updated successfully.")
    except Exception as exc:  # noqa: BLE001
        print(f"\nERROR applying agent: {exc}")
        return 1

    # Confirm.
    rows = session.sql(f"describe agent {fq}").collect()
    if rows:
        print("New default version:", rows[0].as_dict().get("default_version_name"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
