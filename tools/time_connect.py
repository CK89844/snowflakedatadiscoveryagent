"""Time how long a Snowflake connection takes, phase by phase.

Reads .streamlit/secrets.toml so it uses the exact same connection config as
the app. Run with the venv python. If the SSO token is cached (keyring) this
should NOT open a browser and should be fast; if it is slow WITHOUT a popup,
the cost is in TLS/OCSP/network setup rather than the interactive login.
"""

from __future__ import annotations

import time
import tomllib
from pathlib import Path

SECRETS = Path(__file__).resolve().parent.parent / ".streamlit" / "secrets.toml"


def main() -> int:
    cfg = tomllib.loads(SECRETS.read_text(encoding="utf-8"))
    conn = dict(cfg["connections"]["snowflake"])
    if str(conn.get("authenticator", "")).lower() == "externalbrowser":
        conn.setdefault("client_store_temporary_credential", True)
        conn.setdefault("client_request_mfa_token", True)

    import snowflake.connector

    print("Opening connection… (a browser popup here = token was NOT cached)")
    t0 = time.perf_counter()
    cn = snowflake.connector.connect(**conn)
    t1 = time.perf_counter()
    print(f"  connect(): {t1 - t0:6.2f}s")

    cur = cn.cursor()
    t2 = time.perf_counter()
    cur.execute("select current_timestamp")
    cur.fetchone()
    t3 = time.perf_counter()
    print(f"  first query: {t3 - t2:6.2f}s")

    cur.close()
    cn.close()
    print(f"  TOTAL: {t3 - t0:6.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
