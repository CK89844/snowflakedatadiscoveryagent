"""Service-code reference and strategic/legacy classification.

Single source of truth for what each EDW **service code** means and whether the
data behind it is STRATEGIC or LEGACY. Used by:
  * tools/build_catalog.py   - to stamp every catalogue column with a
    SERVICE_CLASSIFICATION so the Cortex Search service can surface it.
  * streamlit_app.py         - to badge candidates in the UI and make it clear,
    when mapping to EDW, which service code a column is sourced from and whether
    that source is strategic or legacy.

Classification rule (from the EDW Service Code Availability confluence export):
  * CRDEF is strategic.
  * Anything sourced from ADP (adp_core), Fundipedia (fundipedia_raw),
    Lipper (lipper_raw) or Charles River Private Markets (crpm_ree_raw) is
    strategic.
  * Phoenix-sourced data (phoenix_raw) is legacy.
  * Anything else is UNKNOWN (surfaced but not asserted either way).

The list below may be incomplete; unknown codes fall through to UNKNOWN rather
than being guessed. Add new codes here as they are confirmed.
"""

from __future__ import annotations

# CDP source schema -> classification.
STRATEGIC_SCHEMAS = {"adp_core", "fundipedia_raw", "lipper_raw", "crpm_ree_raw"}
LEGACY_SCHEMAS = {"phoenix_raw"}

STRATEGIC = "STRATEGIC"
LEGACY = "LEGACY"
MIXED = "MIXED"
UNKNOWN = "UNKNOWN"

# Service code -> (CDP source schema, provider, description).
# Providers/descriptions are best-effort from the confluence export.
SERVICE_CODES: dict[str, tuple[str, str, str]] = {
    # --- ADP (adp_core) : STRATEGIC ---
    "CRDEF": ("adp_core", "CRD", "CR Default"),
    "RIMES": ("adp_core", "RIMES", "RIMES benchmark data"),
    "SSCD_EM": ("adp_core", "SSCD", "SSCD Entity Master"),
    "SSCD-EM": ("adp_core", "SSCD", "SSCD Entity Master"),
    "IMSMOTRX": ("adp_core", "IMSESP", "IMS Middle Office transactions"),
    "IMSMOEODT": ("adp_core", "IMSESP", "IMS Middle Office EOD positions"),
    "IMSMOSODT": ("adp_core", "IMSESP", "IMS Middle Office SOD positions"),
    "IMSMOMET": ("adp_core", "IMSESP", "IMS Middle Office"),
    "IMSESP": ("adp_core", "IMSESP", "IMS Enterprise"),
    "WM11": ("adp_core", "WORLD MARKET FX RATE", "SSDD WM FX Rate"),
    "AXIOMA_LG1": ("adp_core", "Axioma", "Axioma analytics"),
    "AXIOMA_LG2": ("adp_core", "Axioma", "Axioma analytics"),
    "AXIOMA_LG3": ("adp_core", "Axioma", "Axioma JPM analytics"),
    "AXIOMA_LG4": ("adp_core", "Axioma", "Axioma ICE analytics"),
    "AXIOMA_LGI": ("adp_core", "Axioma", "Axioma Main PSA analytics"),
    "PORT_LG1": ("adp_core", "Bloomberg", "JPM - Port"),
    "PORT_LG2": ("adp_core", "Bloomberg", "JPM - Port"),
    "PORT_LG3": ("adp_core", "Bloomberg", "JPM - Port"),
    "PORT_LG4": ("adp_core", "Bloomberg", "Port analytics"),
    "PORT_LG5": ("adp_core", "Bloomberg", "Market Iboxx - Port"),
    "PORT_LG6": ("adp_core", "Bloomberg", "Port analytics"),
    "PORT_LGI": ("adp_core", "Bloomberg", "Port analytics"),
    # --- Fundipedia (fundipedia_raw) : STRATEGIC ---
    "EAM": ("fundipedia_raw", "Fundipedia", "Enterprise Account Master"),
    "EPM": ("fundipedia_raw", "Fundipedia", "Enterprise Product Master"),
    # --- Lipper (lipper_raw) : STRATEGIC ---
    "LIPPER": ("lipper_raw", "Lipper", "Lipper performance data"),
    # --- Phoenix (phoenix_raw) : LEGACY ---
    "QUASAR": ("phoenix_raw", "Quasar", "Legacy Phoenix - Quasar"),
    "SCOPE": ("phoenix_raw", "Scope", "Legacy Phoenix - Scope"),
    "BNYM": ("phoenix_raw", "BNYM", "Legacy Phoenix - Bank of New York Mellon"),
    "NT": ("phoenix_raw", "Northern Trust", "Legacy Phoenix - Northern Trust"),
    "MDMS": ("phoenix_raw", "MDMS", "Legacy Phoenix - MDMS"),
    "LGIM": ("phoenix_raw", "LGIM", "Legacy Phoenix warehouse (LGIM)"),
    "OMS": ("phoenix_raw", "OMS", "Legacy Phoenix - OMS"),
    "PORTWARE": ("phoenix_raw", "Portware", "Legacy Phoenix - Portware"),
    "STATESTREET": ("phoenix_raw", "State Street", "Legacy Phoenix - State Street"),
    # --- Charles River Private Markets (crpm_ree_raw) : STRATEGIC ---
    "CRPM": ("crpm_ree_raw", "Charles River Private Markets", "CRPM real estate"),
}


# Values that appear in the raw SERVICE_CODES data but are NOT real service
# codes (schema/domain labels, placeholders). Stripped before classification.
IGNORE_CODES = {"EDW", "ALL", "N/A", "NONE", "NULL", "-"}


def _norm(code: str) -> str:
    return str(code or "").strip().upper()


def classify_service_code(code: str) -> str:
    """Return STRATEGIC / LEGACY / UNKNOWN for a single service code."""
    ref = SERVICE_CODES.get(_norm(code))
    if not ref:
        return UNKNOWN
    schema = ref[0].lower()
    if schema in STRATEGIC_SCHEMAS:
        return STRATEGIC
    if schema in LEGACY_SCHEMAS:
        return LEGACY
    return UNKNOWN


def service_code_source(code: str) -> str:
    """Return the CDP source schema for a service code, or '' if unknown."""
    ref = SERVICE_CODES.get(_norm(code))
    return ref[0] if ref else ""


def classify_service_codes(codes) -> dict:
    """Classify a collection of service codes for one catalogue column.

    `codes` may be a list, or a comma/semicolon-separated string. Returns a dict:
      {
        classification: STRATEGIC | LEGACY | MIXED | UNKNOWN,
        strategic: [codes...],
        legacy:    [codes...],
        unknown:   [codes...],
        all:       [codes...],
      }
    A column that carries both strategic and legacy codes is MIXED (EDW columns
    frequently blend source systems) so the UI can flag it for a decision.
    """
    if isinstance(codes, str):
        parts = [c for c in _split(codes)]
    else:
        parts = [str(c) for c in (codes or [])]
    seen: list[str] = []
    for c in parts:
        cc = _norm(c)
        if cc and cc not in IGNORE_CODES and cc not in seen:
            seen.append(cc)

    strategic, legacy, unknown = [], [], []
    for c in seen:
        cls = classify_service_code(c)
        if cls == STRATEGIC:
            strategic.append(c)
        elif cls == LEGACY:
            legacy.append(c)
        else:
            unknown.append(c)

    if strategic and legacy:
        classification = MIXED
    elif strategic:
        classification = STRATEGIC
    elif legacy:
        classification = LEGACY
    else:
        classification = UNKNOWN

    return {
        "classification": classification,
        "strategic": strategic,
        "legacy": legacy,
        "unknown": unknown,
        "all": seen,
    }


def _split(s: str) -> list[str]:
    out: list[str] = []
    for chunk in str(s or "").replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk:
            out.append(chunk)
    return out
