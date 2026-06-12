"""DialAgent MCP server — exposes the phone-calling FastAPI app as MCP tools.

This is a thin HTTP client of the FastAPI app. It NEVER imports `server` or
`agent`: live call state lives in the server process, so we keep two processes
with one source of truth and talk over HTTP. The same three tools are served
over stdio (this file's __main__, for Claude Desktop / Cursor / Gemini CLI) and
over streamable HTTP when mounted into the FastAPI app (Phase 6).

Env:
  DIALAGENT_BASE_URL  base URL of the running FastAPI app (default localhost:8000)
  DIALAGENT_SECRET    shared secret, sent as the X-DialAgent-Key header
"""

import os

import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = os.environ.get("DIALAGENT_BASE_URL", "http://localhost:8000")
SECRET = os.environ.get("DIALAGENT_SECRET", "")

mcp = FastMCP("DialAgent")


def _headers() -> dict[str, str]:
    return {"X-DialAgent-Key": SECRET} if SECRET else {}


@mcp.tool()
async def place_call(phone: str, task: str) -> dict:
    """Place a REAL outbound phone call and pursue a plain-English task on it.

    This dials a real phone number and costs real money (~$0.05/min), so only
    call it when the user has clearly asked to place a call — never
    speculatively. Returns immediately with a `call_sid`; the call then runs
    for 1–3 minutes. Poll get_call_status(call_sid) to get the live transcript
    and the final structured answer.

    Args:
        phone: Number to call. Any common US format works (e.g. "(415)
            555-1234"); E.164 like "+14155551234" is ideal.
        task: What to ask, in plain English, e.g. "Ask if they accept Delta
            Dental PPO and what a cleaning costs out of pocket."
    """
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
        r = await client.post(
            "/submit", json={"phone": phone, "task": task}, headers=_headers()
        )
        r.raise_for_status()
        return r.json()


@mcp.tool()
async def get_call_status(call_sid: str, wait_seconds: int = 60) -> dict:
    """Get the live transcript and/or final structured result of a call.

    Calls take 1–3 minutes. This long-polls: it blocks up to `wait_seconds`
    (max 120) and returns as soon as the call reaches a terminal status. Call
    it repeatedly with wait_seconds=60 until `status` is terminal (completed /
    no_answer / busy / failed / error). While the call is active, `transcript`
    contains the turns heard so far.

    Args:
        call_sid: The id returned by place_call.
        wait_seconds: Seconds to wait for a change before returning (0–120).
    """
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=wait_seconds + 15) as client:
        r = await client.get(
            f"/status/{call_sid}", params={"wait": wait_seconds}, headers=_headers()
        )
        r.raise_for_status()
        return r.json()


@mcp.tool()
async def list_recent_calls(limit: int = 10) -> list:
    """List recent calls, newest first.

    Each entry has call_sid, to_number, task, status, created_at, and a
    one-line summary when the call produced one.

    Args:
        limit: Max number of calls to return (default 10).
    """
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30) as client:
        r = await client.get("/calls", params={"limit": limit}, headers=_headers())
        r.raise_for_status()
        return r.json()


if __name__ == "__main__":
    mcp.run()
