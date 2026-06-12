# DialAgent

A voice agent that makes phone calls for you. Today it's a **dental
shopping concierge**: give it an office's number and a question ("do you
take Delta Dental PPO? what's a cleaning cost?") and it calls, navigates
the phone menu, asks, and reports back a structured answer — with the
verbatim quote it's based on, plus a click-to-dial link if it couldn't get
a clear answer.

It's distributed as an **MCP server**, so you use it from inside Claude or
ChatGPT, not a separate app.

---

## Use DialAgent (remote connector)

> **Coming in the connector phase.** Paste-the-URL setup for claude.ai and
> ChatGPT lands here — zero install, zero keys, calls run on the host's
> stack. Until then, use the stdio MCP server or the web form below.

---

## Web form

A secondary surface (nice on a phone). With the server running and tunneled,
open:

```
https://<your-ngrok-domain>/?key=<DIALAGENT_SECRET>
```

Pick a task type, paste the office number, submit. You'll see the live
transcript stream, then a results page with the structured answer, the
quote behind it, and a click-to-dial link to the office.

---

## Run your own instance

For operators / self-hosters — not the typical user path.

### Prerequisites

- Python 3.11
- Accounts + API keys: **Twilio** (with a phone number), **Deepgram**,
  **Anthropic**, **ngrok**. Full details in `docs/stack-setup.md`.

### Setup

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp user_profile.example.json user_profile.json   # then fill in real values
```

Create a `.env` in the repo root (see `docs/stack-setup.md` for the full
list and how to generate the secret):

```
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+1...
JAY_CELL=+1...
DEEPGRAM_API_KEY=...
ANTHROPIC_API_KEY=...
NGROK_URL=https://<your-reserved-domain>
DIALAGENT_SECRET=...        # python -c "import secrets; print(secrets.token_urlsafe(16))"
```

### Run

```bash
uvicorn server:app --port 8000          # the app
ngrok http 8000                         # tunnel; use a reserved domain so NGROK_URL is stable
```

Set `NGROK_URL` to the `https://` URL ngrok prints (Twilio needs a public
webhook). The web form is then at `https://<ngrok>/?key=<DIALAGENT_SECRET>`.

### MCP over stdio (Claude Desktop / Cursor / Gemini CLI)

Add this to the host's MCP config (use an **absolute** path to the repo's
venv python and to `mcp_server.py`), then restart the host:

```json
{
  "mcpServers": {
    "dialagent": {
      "command": "/abs/path/to/DialAgent/.venv/bin/python",
      "args": ["/abs/path/to/DialAgent/mcp_server.py"],
      "env": {
        "DIALAGENT_BASE_URL": "http://localhost:8000",
        "DIALAGENT_SECRET": "<your secret>"
      }
    }
  }
}
```

Then, in the host, just say: *"call (415) 555-1234 and ask if they take
Delta Dental PPO."*

Tools exposed: `place_call(phone, task)`, `get_call_status(call_sid,
wait_seconds)`, `list_recent_calls(limit)`.
