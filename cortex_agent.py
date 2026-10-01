"""
Cortex Agent client
============================================================
Thin wrapper around the Snowflake Cortex Agent REST API so the Streamlit app
can talk to the named agent object (e.g. `cillian_test`) that has been created
in the DEV environment and connected to the semantic views for the new model.

The named-agent run endpoint is:

    POST /api/v2/databases/{database}/schemas/{schema}/agents/{name}:run

We authenticate by reusing the token from the active Snowpark/Snowflake
connection, so the same SSO login used for querying also drives the agent.

The response is a Server-Sent-Events (SSE) stream. We collect the assistant's
text deltas and return the final concatenated answer plus the raw events (in
case the caller wants tool results / citations).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import requests


@dataclass
class AgentResponse:
    """Parsed result of a single agent run."""

    text: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def _account_host(conn) -> str:
    """Return the base https host for the account behind a Snowflake connection."""
    host = getattr(conn, "host", None)
    if host:
        return f"https://{host}"
    # Fallback: build from account (works for most deployments).
    account = getattr(conn, "account", "")
    return f"https://{account}.snowflakecomputing.com"


def _rest_token(conn) -> str:
    """Extract the session token from a live Snowflake connection."""
    rest = getattr(conn, "rest", None) or getattr(conn, "_rest", None)
    token = getattr(rest, "token", None)
    if not token:
        raise RuntimeError(
            "Could not obtain a Snowflake session token from the connection."
        )
    return token


def _raw_connection(session):
    """Get the underlying snowflake.connector connection from a Snowpark session."""
    # Snowpark keeps the connector connection on session._conn._conn
    inner = getattr(session, "_conn", None)
    conn = getattr(inner, "_conn", None) if inner is not None else None
    if conn is None:
        raise RuntimeError("Unable to access the raw Snowflake connection.")
    return conn


def run_agent(
    session,
    agent_name: str,
    database: str,
    schema: str,
    prompt: str,
    timeout: int = 120,
) -> AgentResponse:
    """Run the named Cortex Agent with a single user prompt.

    Parameters
    ----------
    session : snowflake.snowpark.Session
        Active Snowpark session (local SSO or Streamlit-in-Snowflake).
    agent_name, database, schema : str
        Fully qualify the agent object to invoke.
    prompt : str
        The user message sent to the agent.
    timeout : int
        HTTP read timeout in seconds.
    """
    conn = _raw_connection(session)
    host = _account_host(conn)
    token = _rest_token(conn)

    url = (
        f"{host}/api/v2/databases/{database}/schemas/{schema}"
        f"/agents/{agent_name}:run"
    )

    headers = {
        "Authorization": f'Snowflake Token="{token}"',
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    body = {
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt}],
            }
        ]
    }

    result = AgentResponse()
    try:
        with requests.post(
            url, headers=headers, json=body, stream=True, timeout=timeout
        ) as resp:
            if resp.status_code >= 400:
                result.error = f"HTTP {resp.status_code}: {resp.text[:500]}"
                return result
            result.text, result.events = _parse_sse(resp)
    except requests.RequestException as exc:  # network / timeout
        result.error = f"Request failed: {exc}"
    return result


def _parse_sse(resp) -> tuple[str, list[dict[str, Any]]]:
    """Parse an SSE stream into concatenated text + list of raw event payloads."""
    text_parts: list[str] = []
    events: list[dict[str, Any]] = []

    for raw_line in resp.iter_lines(decode_unicode=True):
        if not raw_line or not raw_line.startswith("data:"):
            continue
        data = raw_line[len("data:"):].strip()
        if data in ("", "[DONE]"):
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        events.append(payload)
        text_parts.append(_extract_text(payload))

    return "".join(p for p in text_parts if p), events


def _extract_text(payload: dict[str, Any]) -> str:
    """Best-effort extraction of assistant text from a single SSE event.

    The agent API has evolved across previews, so we defensively look in the
    common shapes: response.text deltas, message content blocks, and choices.
    """
    # Shape 1: {"delta": {"content": [{"type": "text", "text": "..."}]}}
    delta = payload.get("delta")
    if isinstance(delta, dict):
        content = delta.get("content")
        joined = _text_from_content(content)
        if joined:
            return joined
        if isinstance(delta.get("text"), str):
            return delta["text"]

    # Shape 2: {"content": [{"type": "text", "text": "..."}]}
    joined = _text_from_content(payload.get("content"))
    if joined:
        return joined

    # Shape 3: {"text": "..."}
    if isinstance(payload.get("text"), str):
        return payload["text"]

    # Shape 4: OpenAI-style choices
    for choice in payload.get("choices", []) or []:
        d = choice.get("delta") or choice.get("message") or {}
        if isinstance(d.get("content"), str):
            return d["content"]
        joined = _text_from_content(d.get("content"))
        if joined:
            return joined

    return ""


def _text_from_content(content: Any) -> str:
    """Join the text of a list-of-blocks content field."""
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") in (None, "text"):
            t = block.get("text")
            if isinstance(t, str):
                parts.append(t)
    return "".join(parts)
