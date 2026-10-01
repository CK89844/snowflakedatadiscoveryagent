"""
Rename the Cortex Agent object by recreating it under a new name from the
saved specification (tools/agent_spec.json) and dropping the old object.
Snowflake does not support ALTER AGENT ... RENAME, so we clone + drop.

Reuses the same secrets/connection the Streamlit app uses.

Run (dry run):
    .venv\\Scripts\\python.exe tools\\rename_agent.py OLD_NAME NEW_NAME
Run (apply):
    .venv\\Scripts\\python.exe tools\\rename_agent.py OLD_NAME NEW_NAME --apply
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


def load_cfg() -> tuple[dict, dict]:
    with open(SECRETS, "rb") as fh:
        data = tomllib.load(fh)
    conn = dict(data["connections"]["snowflake"])
    agent = dict(data.get("agent", {}))
    if str(conn.get("authenticator", "")).lower() == "externalbrowser":
        conn.setdefault("client_store_temporary_credential", True)
    return conn, agent


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply = "--apply" in sys.argv
    conn, agent = load_cfg()
    db = agent.get("database", "DATA_ENGINEERING_HOME")
    schema = agent.get("schema", "CILLIAN_TEST")

    old = args[0] if len(args) > 0 else "CILLIAN_TEST_AGENT"
    new = args[1] if len(args) > 1 else agent.get("name", "DATA_DISCOVERY_AGENT")

    old_fq = f"{db}.{schema}.{old}"
    new_fq = f"{db}.{schema}.{new}"

    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    spec_json = json.dumps(spec, indent=2)
    create = (
        f"CREATE OR REPLACE AGENT {new_fq}\n"
        "WITH PROFILE='{\"display_name\":\"Data Discovery Agent\"}'\n"
        "COMMENT='Data marketplace agent: search, legacy-report mapping and lineage. Returns a single JSON envelope.'\n"
        f"FROM SPECIFICATION $$\n{spec_json}\n$$"
    )
    drop = f"DROP AGENT IF EXISTS {old_fq}"

    print(f"Connecting to Snowflake as {conn.get('user')} ...")
    session = Session.builder.configs(conn).create()
    ctx = session.sql("select current_account(), current_role()").collect()[0].as_dict()
    print("Connected. Context:", ctx)

    print(f"\nStep 1: create {new_fq} from spec")
    print(f"Step 2: drop {old_fq}")

    if not apply:
        print("\nDRY RUN. Re-run with --apply to execute.")
        return 0

    try:
        session.sql(create).collect()
        print(f"Created {new}.")
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR creating {new}: {exc}")
        return 1

    try:
        session.sql(drop).collect()
        print(f"Dropped {old}.")
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: could not drop {old}: {exc}")

    rows = session.sql(f"show agents in schema {db}.{schema}").collect()
    print("Agents now in schema:", [r.as_dict().get("name") for r in rows])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
