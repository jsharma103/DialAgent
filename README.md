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

The primary way to use DialAgent: add it as a custom connector in your LLM
host. Zero install, zero keys — the calls run on the operator's stack. You
just need the connector URL (it embeds the secret):

```
https://<ngrok-domain>/connector/<DIALAGENT_SECRET>/mcp
```

**Add to claude.ai** (web, desktop, or mobile — the connection comes from
Anthropic's cloud, so it works everywhere once added):
Settings → Connectors → **Add custom connector** → paste the URL.

**Add to ChatGPT** (Plus/Pro required):
Settings → Apps → Advanced settings → **Developer mode** → add a connector →
paste the URL.

**Gemini:** the consumer Gemini app does **not** support custom MCP
connectors. Gemini users go through the Gemini CLI via the stdio config in
"Run your own instance" below.

Then just say, in chat: *"call (415) 555-1234 and ask if they take Delta
Dental PPO."* The host calls `place_call`, polls `get_call_status`, and
renders the structured answer.

**Caveats:**
- The operator's laptop must be awake with the server **and** ngrok running.
- Every connected person's calls run on the operator's Twilio / Deepgram /
  Anthropic keys (and cost ~$0.05/min).
- Anyone with the URL can place calls. To revoke everyone, rotate
  `DIALAGENT_SECRET` (the connector URL changes with it).

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
