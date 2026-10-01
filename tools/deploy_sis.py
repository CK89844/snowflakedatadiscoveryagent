"""
Deploy the Data Discovery & Mapping app to Streamlit-in-Snowflake (SiS).

Reuses the same secrets/connection the Streamlit app uses (SSO externalbrowser),
so no Snowflake CLI is required. It:

  1. Creates the stage DATA_ENGINEERING_HOME.CILLIAN_TEST.STREAMLIT_STAGE.
  2. PUTs the app files (streamlit_app.py, cortex_agent.py, service_codes.py,
     environment.yml, docs/how_it_works.html) onto the stage.
  3. CREATE OR REPLACE STREAMLIT DATA_DISCOVERY_APP.
  4. Grants USAGE so the producer role can open it.

Run (dry run, prints what it would do):
    .venv\\Scripts\\python.exe tools\\deploy_sis.py
Run (actually deploy):
    .venv\\Scripts\\python.exe tools\\deploy_sis.py --apply
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

from snowflake.snowpark import Session

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / ".streamlit" / "secrets.toml"

APP_NAME = "REPORT_MAPPING_TOOL"
STAGE = "STREAMLIT_STAGE"
MAIN_FILE = "streamlit_app.py"
# Container-based (SPCS) Streamlit runs on a compute pool, not a warehouse.
# The query warehouse is still used for the SQL the app issues.
COMPUTE_POOL = "CORTEX_STREAMLIT_COMPUTE_POOL"

# (local relative path, stage sub-path).  '' = stage root.
# Container (vNext) Streamlit on a compute pool resolves packages via pip from
# the account's attached artifact repository (SNOWFLAKE.SNOWPARK.PYPI_SHARED_
# REPOSITORY) using pyproject.toml — no public internet / EAI required. The
# warehouse-only environment.yml must NOT be present (it conflicts with vNext).
FILES = [
    ("streamlit_app.py", ""),
    ("cortex_agent.py", ""),
    ("service_codes.py", ""),
    ("pyproject.toml", ""),
    ("docs/how_it_works.html", "docs"),
]


def load_cfg() -> dict:
    with open(SECRETS, "rb") as fh:
        data = tomllib.load(fh)
    conn = dict(data["connections"]["snowflake"])
    if str(conn.get("authenticator", "")).lower() == "externalbrowser":
        conn.setdefault("client_store_temporary_credential", True)
    return conn


def _put(session, local: Path, stage_subpath: str, apply: bool) -> None:
    # Snowpark PUT needs a forward-slash file:// URI; stage paths use '/'.
    uri = local.resolve().as_posix()
    target = f"@{STAGE}" + (f"/{stage_subpath}" if stage_subpath else "")
    sql = (
        f"PUT 'file://{uri}' {target} "
        "OVERWRITE = TRUE AUTO_COMPRESS = FALSE SOURCE_COMPRESSION = NONE"
    )
    print(f"  PUT {local.name} -> {target}")
    if apply:
        res = session.sql(sql).collect()
        if res:
            r = res[0].as_dict()
            print(f"       {r.get('source')} : {r.get('status')}")


def main() -> int:
    apply = "--apply" in sys.argv
    conn = load_cfg()
    db = conn.get("database", "DATA_ENGINEERING_HOME")
    schema = conn.get("schema", "CILLIAN_TEST")
    role = conn.get("role", "DATA_ENGINEERING_HOME_PRODUCER_ROLE")
    wh = conn.get("warehouse", "ENGINEERING_WH")

    print(f"Connecting to Snowflake as {conn.get('user')} …")
    session = Session.builder.configs(conn).create()
    ctx = (
        session.sql("select current_account(), current_role(), current_warehouse()")
        .collect()[0]
        .as_dict()
    )
    print("Connected. Context:", ctx)

    session.sql(f"USE DATABASE {db}").collect()
    session.sql(f"USE SCHEMA {schema}").collect()

    mode = "APPLY" if apply else "DRY-RUN (add --apply to execute)"
    print(f"\n=== Deploying {db}.{schema}.{APP_NAME}  [{mode}] ===\n")

    # 1) Stage -----------------------------------------------------------------
    stage_sql = (
        f"CREATE STAGE IF NOT EXISTS {STAGE} DIRECTORY = (ENABLE = TRUE) "
        "COMMENT = 'Source files for the Data Discovery Streamlit app.'"
    )
    print(f"Stage: {STAGE}")
    if apply:
        session.sql(stage_sql).collect()
        # environment.yml is warehouse-runtime only and breaks vNext/container
        # apps — strip it so the container resolves from pyproject.toml.
        try:
            session.sql(f"REMOVE @{STAGE}/environment.yml").collect()
            print("  Removed stale environment.yml from stage")
        except Exception as exc:  # noqa: BLE001
            print(f"  environment.yml cleanup note: {str(exc)[:120]}")

    # 2) Upload files ----------------------------------------------------------
    print("\nUploading files:")
    missing = []
    for rel, sub in FILES:
        local = ROOT / rel
        if not local.exists():
            missing.append(rel)
            print(f"  MISSING: {rel}")
            continue
        _put(session, local, sub, apply)
    if missing:
        print(f"\nERROR: missing files, aborting: {missing}")
        return 2

    # 3) Container (vNext) runtime on a compute pool. vNext apps MUST use
    #    FROM '@stage' (a directory/versioned stage), NOT ROOT_LOCATION (which
    #    is warehouse-runtime only and is rejected by vNext). Packages resolve
    #    via pip from the account's attached PyPI artifact repository using
    #    pyproject.toml — no public internet / EAI required.
    st_sql = (
        f"CREATE OR REPLACE STREAMLIT {APP_NAME} "
        f"FROM '@{db}.{schema}.{STAGE}' "
        f"MAIN_FILE = '{MAIN_FILE}' "
        f"COMPUTE_POOL = {COMPUTE_POOL} "
        f"QUERY_WAREHOUSE = {wh} "
        "TITLE = 'Report Mapping Tool'"
    )
    print(f"\nStreamlit object (container/compute pool): {APP_NAME}")
    print(f"  {st_sql}")
    if apply:
        session.sql(st_sql).collect()

    # 5) Grant usage -----------------------------------------------------------
    grant_sql = f"GRANT USAGE ON STREAMLIT {APP_NAME} TO ROLE {role}"
    print(f"\nGrant: {grant_sql}")
    if apply:
        session.sql(grant_sql).collect()

    # 6) Verify it landed ------------------------------------------------------
    if apply:
        rows = session.sql(f"SHOW STREAMLITS LIKE '{APP_NAME}'").collect()
        url_id = rows[0].as_dict().get("url_id") if rows else ""
        print(f"\nRuntime: container on compute pool {COMPUTE_POOL} "
              f"(query warehouse {wh}).")
        print("Snowsight URL name:", url_id)
        print(f"\nDone. Open Snowsight > Projects > Streamlit > {APP_NAME}.")
    else:
        print("\nDry run complete. Re-run with --apply to deploy.")
    session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
