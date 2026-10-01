# Snowflake Data Discovery Agent

A Streamlit app that maps **legacy report attributes** onto a **new semantic
model** using a Snowflake **Cortex Agent** (`cillian_test`) that is connected to
the target semantic views.

## What it does

1. Upload a legacy report (CSV or Excel).
2. The app profiles each column (name, data type, a few sample values).
3. For each attribute it asks the Cortex Agent to find the best-matching field
   in the new semantic model.
4. You review, edit and export the mapping as CSV.

## Project layout

| File | Purpose |
|------|---------|
| `streamlit_app.py` | The UI and mapping workflow. |
| `cortex_agent.py` | Client that calls the named agent's REST `:run` endpoint. |
| `requirements.txt` | Python dependencies. |
| `.streamlit/secrets.toml.example` | Template for local SSO + agent config. |

## Run locally (SSO)

```powershell
# 1. Create and activate a virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure secrets
Copy-Item .streamlit\secrets.toml.example .streamlit\secrets.toml
# then edit .streamlit\secrets.toml with your DEV account details

# 4. Run
streamlit run streamlit_app.py
```

Login uses `authenticator = "externalbrowser"`, so a browser window opens for
SSO. No password is stored.

## Configuration

Everything lives in `.streamlit/secrets.toml`:

- `[connections.snowflake]` — account, user, role, warehouse, database, schema.
- `[agent]` — the agent object to call (`name`, `database`, `schema`).

The app authenticates the agent REST call by reusing the token from your active
Snowflake session, so the same SSO login drives both queries and the agent.

## Notes

- The agent is expected to return a compact JSON object per attribute:
  `{"target_view", "target_attribute", "confidence", "rationale"}`. The prompt
  in `streamlit_app.py` requests exactly this; adjust it if your agent is tuned
  differently.
- When deployed as **Streamlit-in-Snowflake**, the app uses the active session
  automatically — no secrets file needed.
