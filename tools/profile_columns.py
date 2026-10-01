"""
Value-profile every column in EDW_CORE (Silver) and DATA_PRODUCT_CORE (Gold)
to enable value + pattern matching (Options 1 & 3) when attribute names don't
obviously match.

For speed, each table is filtered to its LATEST snapshot via the best available
as-of date column (AS_OF_DATE > POSITION_DATE > BATCH_DATE > ... ) before
profiling.

Per column we capture:
  - DISTINCT_COUNT  (APPROX_COUNT_DISTINCT)         [Option 1]
  - NULL_RATE
  - MIN_VALUE / MAX_VALUE (scalar types only)
  - SAMPLE_VALUES  (up to 20 distinct, ONLY for low-cardinality coded columns)  [Option 1]
  - VALUE_PATTERN  (regex signature: ISIN, LEI, SEDOL, CUSIP, ISO country/ccy,
                    date, integer, decimal, uuid, code, text)                   [Option 3]
  - AS_OF_DATE     (the snapshot the profile was taken from)

Reads tools/core_inventory.json for the table/column list and their datatypes.
Writes tools/core_profile.json keyed by "SCHEMA.TABLE.COLUMN".

Run (dry run - lists what it will profile):
    .venv\\Scripts\\python.exe tools\\profile_columns.py
Run (apply - connects and profiles):
    .venv\\Scripts\\python.exe tools\\profile_columns.py --apply
Optional: limit tables while testing:
    .venv\\Scripts\\python.exe tools\\profile_columns.py --apply --max-tables 3
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from datetime import date, timedelta
from pathlib import Path

from snowflake.snowpark import Session

# Legacy constant (no longer used for filtering — kept for reference). Each
# table is now profiled at its OWN latest snapshot via MAX(as_of).
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / ".streamlit" / "secrets.toml"
INVENTORY = Path(__file__).resolve().parent / "core_inventory.json"
OUT = Path(__file__).resolve().parent / "core_profile.json"
EXCLUDED = Path(__file__).resolve().parent / "profile_excluded.json"
PROGRESS = Path(__file__).resolve().parent / "profile_progress.json"

# Skip tables above this row count — they are too slow to sample and are logged
# to profile_excluded.json instead. Override with --max-rows N.
MAX_ROWS = 50_000_000

# Profile the curated layers plus any raw (Bronze) schema discovered by the
# inventory (DATA_MARKETPLACE.*_RAW). The curated pair is listed explicitly; raw
# schemas are matched dynamically by the is_raw flag / _RAW suffix below.
CANONICAL = {
    ("DATA_MARKETPLACE", "DATA_PRODUCT_CORE"),  # Gold
    ("DATA_MARKETPLACE", "EDW_CORE"),            # Silver
}

# Preferred snapshot/as-of columns, highest priority first.
AS_OF_CANDIDATES = [
    "AS_OF_DATE",
    "POSITION_DATE",
    "VALUATION_DATE",
    "REPORTING_DATE",
    "COB_DATE",
    "BUSINESS_DATE",
    "SNAPSHOT_DATE",
    "BATCH_DATE",
    "DATA_DATE",
    "LOAD_DATE",
]

# Datatypes we can safely MIN/MAX/APPROX_COUNT_DISTINCT and sample as text.
SCALAR_TYPES = {
    "TEXT", "STRING", "VARCHAR", "CHAR",
    "NUMBER", "DECIMAL", "INT", "INTEGER", "BIGINT", "SMALLINT",
    "FLOAT", "DOUBLE", "REAL",
    "DATE", "TIMESTAMP_NTZ", "TIMESTAMP_LTZ", "TIMESTAMP_TZ", "TIMESTAMP",
    "BOOLEAN",
}

LOW_CARD_THRESHOLD = 50    # store distinct sample values only below this
SAMPLE_ROWS = 500          # rows pulled per table to infer patterns / low-card values
MAX_SAMPLE_VALUES = 20     # cap stored distinct values per column
AGG_CHUNK = 60             # columns per aggregate query

# EDW tables mix multiple source systems in one SERVICE_CD column. We sample
# each source separately so every source's values/patterns are represented
# (a flat top-N sample can be dominated by a single source).
SERVICE_COL = "SERVICE_CD"
PER_SERVICE_ROWS = 200     # rows sampled per distinct SERVICE_CD
MAX_SERVICES = 20          # cap distinct sources profiled per table


# --------------------------- pattern inference ---------------------------
_PATTERNS = [
    ("ISIN", re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")),
    ("LEI", re.compile(r"^[A-Z0-9]{18}[0-9]{2}$")),
    ("CUSIP", re.compile(r"^[0-9A-Z]{9}$")),
    ("SEDOL", re.compile(r"^[0-9A-Z]{7}$")),
    ("UUID", re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")),
    ("EMAIL", re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")),
    ("ISO_COUNTRY", re.compile(r"^[A-Z]{2}$")),
    ("ISO_CCY", re.compile(r"^[A-Z]{3}$")),
    ("DATE", re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}:\d{2})?")),
    ("INTEGER", re.compile(r"^-?\d+$")),
    ("DECIMAL", re.compile(r"^-?\d+\.\d+$")),
    ("YN_FLAG", re.compile(r"^[YNyn]$")),
    ("CODE", re.compile(r"^[A-Z0-9_]{1,12}$")),
]


def infer_pattern(values: list[str]) -> str:
    """Return the tightest pattern label that fits most sampled values."""
    vals = [v for v in values if v not in (None, "")]
    if not vals:
        return ""
    # ISO_CCY vs ISO_COUNTRY: 3 vs 2 letters, handled by order below.
    for label, rx in _PATTERNS:
        hits = sum(1 for v in vals if rx.match(str(v).strip()))
        if hits >= max(1, int(0.8 * len(vals))):
            return label
    # Fallback: describe by length band.
    lengths = [len(str(v)) for v in vals]
    return f"TEXT(len~{min(lengths)}-{max(lengths)})"


# --------------------------- helpers ---------------------------
def load_conn() -> dict:
    with open(SECRETS, "rb") as fh:
        data = tomllib.load(fh)
    conn = dict(data["connections"]["snowflake"])
    if str(conn.get("authenticator", "")).lower() == "externalbrowser":
        conn.setdefault("client_store_temporary_credential", True)
    return conn


def base_type(t: str) -> str:
    return re.split(r"[(\s]", str(t).upper(), 1)[0]


def canonical_tables() -> list[dict]:
    inv = json.loads(INVENTORY.read_text(encoding="utf-8"))
    tables = []
    for meta in inv.values():
        is_raw = bool(meta.get("is_raw")) or str(meta.get("schema", "")).upper().endswith("_RAW")
        if (meta["database"], meta["schema"]) in CANONICAL or is_raw:
            tables.append(meta)
    tables.sort(key=lambda m: (m["schema"], m["table"]))
    return tables


def pick_as_of(columns: list[dict]) -> str | None:
    names = {c["name"].upper() for c in columns}
    for cand in AS_OF_CANDIDATES:
        if cand in names:
            return cand
    return None


def q(session: Session, sql: str):
    return session.sql(sql).collect()


# --------------------------- per-table profiling ---------------------------
def profile_table(session: Session, meta: dict) -> dict:
    """Profile a table from a single LIMIT-N sample of its latest snapshot.

    A sample-based pass keeps every table fast and stall-free (one lightweight
    query) while giving us everything value/pattern matching needs: a value
    pattern per column and distinct example values for low-cardinality coded
    columns. Cardinality/null-rate are estimated within the sample.
    """
    db, schema, table = meta["database"], meta["schema"], meta["table"]
    fq = f'{db}.{schema}."{table}"'
    cols = meta["columns"]
    scalar_cols = [c for c in cols if base_type(c["type"]) in SCALAR_TYPES]
    if not scalar_cols:
        return {}
    as_of = pick_as_of(cols)

    # Per-table latest snapshot: filter to the table's OWN most-recent as-of
    # date (MAX of the chosen as-of column), not a fixed global "yesterday".
    # Tables have different cadences (daily / weekly / month-end / static
    # reference), so a fixed date leaves most of them empty. One cheap MAX()
    # scan per table finds the real latest snapshot and makes reference/master
    # tables profile properly. Falls back to unfiltered if MAX is unavailable.
    as_of_value = None
    if as_of:
        try:
            mx_rows = q(session, f'SELECT MAX("{as_of}") AS M FROM {fq}')
            mx = mx_rows[0].as_dict().get("M") if mx_rows else None
            as_of_value = str(mx) if mx is not None else None
        except Exception:  # noqa: BLE001 — fall back to unfiltered
            as_of_value = None
    if as_of and as_of_value:
        av = as_of_value.replace("'", "''")
        latest = f"(SELECT * FROM {fq} WHERE \"{as_of}\" = '{av}')"
    else:
        latest = fq
        as_of = None  # nothing usable — profile unfiltered

    # One sample query for the whole table.
    per_col: dict[str, list] = {c["name"]: [] for c in scalar_cols}
    n_sample = 0
    col_list = ", ".join(f'TO_VARCHAR("{c["name"]}") AS "{c["name"]}"' for c in scalar_cols)
    col_names = {c["name"].upper() for c in scalar_cols}
    has_service = SERVICE_COL in col_names

    # Discover the source systems present (ignoring nulls) so each is sampled.
    services: list[str] = []
    if has_service:
        try:
            svc_rows = q(
                session,
                f'SELECT DISTINCT "{SERVICE_COL}" AS S FROM {latest} '
                f'WHERE "{SERVICE_COL}" IS NOT NULL LIMIT {MAX_SERVICES}',
            )
            services = [
                str(r.as_dict().get("S"))
                for r in svc_rows
                if r.as_dict().get("S") is not None
            ]
        except Exception:
            services = []

    rows: list = []
    try:
        if services:
            # Stratified: sample each source system separately, then combine.
            for s in services:
                sval = s.replace("'", "''")
                rr = q(
                    session,
                    f"SELECT {col_list} FROM {latest} "
                    f"WHERE \"{SERVICE_COL}\" = '{sval}' LIMIT {PER_SERVICE_ROWS}",
                )
                rows.extend(rr)
        else:
            rows = q(session, f"SELECT {col_list} FROM {latest} LIMIT {SAMPLE_ROWS}")
        n_sample = len(rows)
        for rr in rows:
            for k, v in rr.as_dict().items():
                per_col[k].append(v)
    except Exception as exc:  # noqa: BLE001
        print(f"    sample failed on {schema}.{table}: {exc}")
        return {}

    result: dict[str, dict] = {}
    for c in scalar_cols:
        col = c["name"]
        vals = per_col.get(col, [])
        non_null = [v for v in vals if v is not None]   # nulls ignored for profiling
        null_rate = round(1 - len(non_null) / n_sample, 4) if n_sample else None
        distinct = list(dict.fromkeys(non_null))  # preserve order, unique
        dc_sample = len(distinct)
        pattern = infer_pattern(non_null)
        sample_values = distinct[:MAX_SAMPLE_VALUES] if dc_sample <= LOW_CARD_THRESHOLD else []
        # Approx min/max from the non-null sample (string/date/number as text).
        mn = min(non_null) if non_null else None
        mx = max(non_null) if non_null else None
        result[col] = {
            "distinct_count": dc_sample,           # within-sample estimate
            "distinct_is_sample": True,
            "null_rate": null_rate,                # within-sample estimate
            "min": mn,
            "max": mx,
            "sample_values": sample_values,
            "value_pattern": pattern,
            "service_codes": services,             # source systems profiled
            "as_of_date": as_of_value,
            "as_of_column": as_of,
            "sample_rows": n_sample,
        }
    return result


def main() -> int:
    apply = "--apply" in sys.argv
    resume = "--resume" in sys.argv
    max_tables = None
    if "--max-tables" in sys.argv:
        max_tables = int(sys.argv[sys.argv.index("--max-tables") + 1])
    max_rows = MAX_ROWS
    if "--max-rows" in sys.argv:
        max_rows = int(sys.argv[sys.argv.index("--max-rows") + 1])

    tables = canonical_tables()
    if max_tables:
        tables = tables[:max_tables]
    total_cols = sum(len(t["columns"]) for t in tables)
    print(f"Candidate tables: {len(tables)}  (~{total_cols} columns)  skip > {max_rows:,} rows")
    for t in tables[:5]:
        rc = t.get("row_count")
        print(f"  e.g. {t['schema']}.{t['table']} ({len(t['columns'])} cols) "
              f"rows={rc if rc is not None else '?'} as-of={pick_as_of(t['columns'])}")

    if not apply:
        print("\nDRY RUN. Re-run with --apply to profile.")
        return 0

    # Resume: load any existing profile and skip tables already fully present.
    profile: dict[str, dict] = {}
    done_tables: set[str] = set()
    if resume and OUT.exists():
        try:
            profile = json.loads(OUT.read_text(encoding="utf-8"))
            done_tables = {".".join(k.split(".")[:2]) for k in profile}
            print(f"Resuming: {len(profile)} column profiles already present "
                  f"across {len(done_tables)} tables.")
        except Exception:  # noqa: BLE001
            profile, done_tables = {}, set()

    conn = load_conn()
    print(f"\nConnecting as {conn.get('user')} ...")
    session = Session.builder.configs(conn).create()
    print("Connected.", session.sql("select current_role()").collect()[0].as_dict())
    # Guard: no single profiling query may run longer than this.
    session.sql("ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = 120").collect()

    excluded: list[dict] = []

    def write_progress(idx: int, current: str) -> None:
        PROGRESS.write_text(json.dumps({
            "done": idx,
            "total": len(tables),
            "current": current,
            "profiled_columns": len(profile),
            "excluded": excluded,
        }, indent=2, default=str), encoding="utf-8")

    for n, meta in enumerate(tables, 1):
        schema, table = meta["schema"], meta["table"]
        key = f"{schema}.{table}"
        row_count = meta.get("row_count")

        # Skip tables above the row ceiling — too slow to sample. Log + continue.
        if isinstance(row_count, int) and row_count > max_rows:
            print(f"[{n}/{len(tables)}] SKIP {key} — {row_count:,} rows > {max_rows:,}")
            excluded.append({
                "table": key, "row_count": row_count,
                "bytes": meta.get("bytes"), "reason": f"row_count > {max_rows}",
            })
            write_progress(n, key)
            continue

        # Resume: skip tables already profiled in a prior run.
        if resume and key in done_tables:
            print(f"[{n}/{len(tables)}] skip {key} (already profiled)")
            write_progress(n, key)
            continue

        print(f"[{n}/{len(tables)}] profiling {key} "
              f"(rows={row_count if row_count is not None else '?'}) ...", flush=True)
        write_progress(n, key)
        try:
            cols = profile_table(session, meta)
        except Exception as exc:  # noqa: BLE001
            print(f"    ! table failed: {exc}")
            excluded.append({
                "table": key, "row_count": row_count,
                "bytes": meta.get("bytes"), "reason": f"error: {str(exc)[:160]}",
            })
            cols = {}
        for col, fp in cols.items():
            profile[f"{schema}.{table}.{col}"] = fp
        # Incremental save so a long run is resumable/inspectable.
        OUT.write_text(json.dumps(profile, indent=2, default=str), encoding="utf-8")
        EXCLUDED.write_text(json.dumps(excluded, indent=2, default=str), encoding="utf-8")

    EXCLUDED.write_text(json.dumps(excluded, indent=2, default=str), encoding="utf-8")
    write_progress(len(tables), "DONE")
    print(f"\nWrote {len(profile)} column profiles -> {OUT}")
    print(f"Excluded {len(excluded)} tables -> {EXCLUDED}")
    if excluded:
        print("Excluded tables:")
        for e in excluded:
            rc = e.get("row_count")
            print(f"  - {e['table']}  ({rc:,} rows)  {e['reason']}"
                  if isinstance(rc, int) else f"  - {e['table']}  {e['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
