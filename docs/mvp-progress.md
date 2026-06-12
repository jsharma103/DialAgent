# MVP progress — morning handoff

Per-phase status for the MVP build (`docs/mvp-plan.md`). Updated as each
phase's gate passes. Unattended rules honored throughout: **no real
phone calls**, work on `main`, one commit per phase, `.env` append-only.

Legend: ✅ done · 🚧 in progress · ⛔ blocked · ⏭️ skipped

| Phase | What | Status | Gate evidence |
|---|---|---|---|
| 0 | Foundations: `agent.py` split, requirements fix | ✅ (1 sub-gate deferred) | imports clean w/o env; fresh `/tmp` venv install + import OK |
| 1 | Call lifecycle | ✅ | `pytest tests/test_lifecycle.py tests/test_observer.py` → 27 passed |
| 2 | Hang-up prep (code only) | ✅ | frame-path trace written; timeout 1200→600; live confirm = morning #4 |
| 3 | Trust: evidence + confidence + evals | 🟡 code ✅ (70 pytest green); LLM gate ⛔ needs API credits | `pytest tests/` → 70 passed; `eval.py --extraction` plumbing verified (0/6 — billing 400 only) |
| 4 | MCP server + endpoints + README | ✅ | `pytest tests/` → 36 passed |
| 5 | Form secret | ✅ | `pytest tests/` → 44 passed |
| 6 | Remote connector surface | ✅ | `pytest tests/` → 45 passed (connector handshake + tools/list + wrong-secret 404) |
| E | Morning checklist (Jay, real calls) | — | handed off — needs Jay |

---

## ⚠️ Environment findings & blockers (read first)

These are deviations from the plan's stated assumptions, discovered at start.

1. **No `.venv` existed in this checkout.** The plan assumed an existing
   `.venv` to develop in and to `pip freeze` known-good pins from. There
   was none, and the deps weren't installed anywhere (pyenv base 3.11.10
   was bare). Built a fresh `.venv` at the project root from pyenv
   **3.11.10** and installed deps (pip installs are allowed by protocol
   rule 2). The "exact pins from known-good env" step was done from this
   freshly-built env instead.

2. **`REQUESTS_CA_BUNDLE` pointed at a stale path.** `~/.zshrc:6` exports
   `REQUESTS_CA_BUNDLE=/tmp/nscacert_combined.pem` (Netskope combined
   bundle), but that `/tmp` file was gone (cleared), so pip had no TLS CA
   bundle. Rebuilt it = public roots (certifi) + Netskope CA
   (`/Library/Application Support/Netskope/STAgent/data/nscacert.pem`) +
   tenant cert (`nstenantcert.pem`). TLS to pypi verified OK (149 certs).
   **No repo files changed.** Latent issue for Jay: the `/tmp` path does
   not survive reboot — consider pointing `REQUESTS_CA_BUNDLE` at a
   persistent location.

3. **Credentials (updated 2026-06-12 morning).** Root cause found: this
   working copy was **cloned from GitHub on May 28 12:15** (see
   `git reflog`); the original work — and the full `.env` — lives on the
   personal MacBook Pro (`jay@Jays-MacBook-Pro.local`, home `/Users/jay`).
   Gitignored files (`.env`, `.venv/`, `user_profile.json`, `calls/`)
   never traveled with the clone. Since then:
   - Found an Anthropic key at `~/Desktop/api_key`; created `.env`
     (gitignored) with `ANTHROPIC_API_KEY`, a freshly generated
     `DIALAGENT_SECRET`, and `SSL_CERT_FILE=/tmp/nscacert_combined.pem`
     (Netskope). Created a placeholder `user_profile.json`.
   - **The key authenticates but the account has NO API credits**
     (billing 400: "credit balance is too low"). TLS + auth verified —
     the request reaches Anthropic and comes back with a request id.
   - **⛔ Still blocked on credits:** Phase 3's LLM gates (full eval
     suite + extraction fixtures) and Phase 0.1's "run one scenario"
     sub-gate. **Action for Jay:** either add credits to this key's
     account (console.anthropic.com → Plans & Billing) or copy the
     funded `.env` from the personal Mac (then re-append
     `DIALAGENT_SECRET` + `SSL_CERT_FILE` lines from the current one).
     Then run:
     ```
     .venv/bin/python eval.py --extraction   # gate: 6/6
     .venv/bin/python eval.py                # gate: 14/14 green
     ```
   - Twilio/Deepgram/ngrok creds (needed only for the morning real-call
     checklist) exist solely in the personal-Mac `.env` or the provider
     dashboards.

---

## Phase 0 — foundations ✅ (1 sub-gate deferred on credentials)

**0.1 — `agent.py` split out of `server.py`.** Done. `agent.py` holds
`SYSTEM_PROMPT_TEMPLATE`/`render_system_prompt`/`format_profile_block`,
the schemas (`END_CALL_SCHEMA`, `SEND_DTMF_SCHEMA`, `REPORT_TOOL`),
`TASK_TEMPLATES`, `extract_result`, `save_call_record`, `require_env`,
model constants (`CALL_MODEL`/`EXTRACT_MODEL`), `EST_COST_PER_MIN`, and a
lazy module-cached `get_user_profile()` + env-overridable
`get_calls_dir()` (reads `DIALAGENT_CALLS_DIR` at call time, not import).
**No import-time side effects** (no dotenv, no logger, no env reads, no
profile load). `server.py` keeps FastAPI/Pipecat/Twilio/SSE/observer and
imports from `agent`; `load_dotenv(override=True)` → `load_dotenv()`.
`eval.py` repointed `server.*` → `agent.*` and no longer imports `server`.

Gate evidence (existing `.venv`, no `.env`/`user_profile.json` present):
```
import agent: OK
import eval: OK
import server: OK
eval imports server? -> server in sys.modules: False
```

**0.2 — reproducible install + housekeeping.** Done. `requirements.txt`
now pins `anthropic==0.109.1`, `loguru==0.7.3`, adds `pytest==9.0.3`,
`mcp==1.27.2`, `httpx==0.28.1`, and gives pipecat its extras:
`pipecat-ai[anthropic,deepgram,silero,websocket,runner]==1.2.1` (extras
chosen by reading the package's declared `Provides-Extra`, covering
Anthropic LLM / Deepgram STT+TTS / Silero VAD / FastAPI-websocket
transport / telephony runner; silero pulls `onnxruntime`, not torch).
Deepgram TTS deprecation fixed:
`DeepgramTTSService(settings=DeepgramTTSService.Settings(voice="aura-2-thalia-en"))`.

Gate evidence (fresh scratch venv `/tmp/dialagent_scratch_venv`, Python
3.11.10, `pip install -r requirements.txt`, stub env):
```
import server, agent, eval: OK   (exit 0)
```
Scratch venv deleted after.

**Deferred (⛔ credentials):** 0.1's "eval harness runs a single
scenario" — needs `ANTHROPIC_API_KEY`. Will run once a key is available.

## Phase 1 — call lifecycle ✅

**1.1 Record schema v2** — `new_call_record()` in `agent.py` builds the
13-field stub (`call_sid, to_number, task, task_type, context, status,
created_at, ended_at, duration_s, est_cost_usd, transcript, result,
error`). `/submit` now: normalize phone → `place_twilio_call` (502 if it
raises, no record) → write `dialing` stub → create `LIVE_EVENTS` queue.
`normalize_phone()` strips spaces/dashes/dots/parens, prepends `+1`
(10-digit) / `+` (11-digit leading 1), validates `^\+\d{8,15}$` else
`ValueError`→400. Timestamps `datetime.now(timezone.utc).isoformat()`.
`finalize_timing()` stamps `ended_at`/`duration_s`/`est_cost_usd`
(prefers Twilio `CallDuration`, else wall-clock since `created_at`;
`est_cost_usd = round(duration_s/60 * 0.05, 2)`).

**1.2 Crash-proof `run_bot`** — post-pipeline work wrapped in
try/except/finally: success path writes `extracting`→`completed`
(extraction failure is caught locally, leaving status `completed` with
`error` set — the *call* succeeded, extraction is best-effort); any
unhandled exception → `error` + exception string; `finally` always
pushes `None` to the live queue and pops the `ACTIVE_CALLS` entry. Status
persisted to disk at each transition (`dialing`→`in_progress` at WS
connect, `extracting` at pipeline end, terminal at final save) via
read-modify-write of the stub.

**1.3 `/call-status` callback** — kills the no-answer black hole.
`place_twilio_call` registers `status_callback=<ngrok>/call-status`,
event `completed`. Endpoint (form-encoded, `CallSid`/`CallStatus`/
`CallDuration`): unknown sid or already-terminal → 204 no-op; record in
`dialing` (WS never connected) → maps `no-answer`→`no_answer`,
`busy`→`busy`, `failed`/`canceled`→`failed`, `completed`→`error`
("media stream never connected"), stamps timing, pushes `None`; record
`in_progress`/`extracting` → WS owns status, only backfills
`duration_s`/`est_cost_usd` if missing.

**1.4 Live registry** — `ACTIVE_CALLS: dict[str, TurnLogObserver]` in
`server.py`, registered at WS connect, removed in `run_bot` `finally`.
Source of truth for partial transcripts (Phase 4 reads it).

**1.5 Tool calls in transcript** — `TurnLogObserver.log_tool(text)`
appends `{role: agent, text}` + emits to the live queue;
`send_dtmf_handler`→`[PRESSED: <digits>]`, `end_call_handler`→
`[ENDED CALL: <reason>]`. Observer moved above the handlers in `run_bot`.

Gate evidence:
```
pytest tests/test_lifecycle.py tests/test_observer.py -q
27 passed, 1 warning in 1.86s
```
Tests use httpx `ASGITransport`; `place_twilio_call` monkeypatched,
`DIALAGENT_CALLS_DIR`→tmp; no network, no real calls.

**Deviation:** `static/index.html` changes for non-`completed` terminal
states (the "No one answered" + click-to-dial CTA from 1.3) are bundled
into **Phase 3.1**, where the results view is rewritten anyway for
evidence rendering + the low-confidence fallback CTA — avoids editing
the same view twice. Verified together in morning checklist rows 2 & 5.

## Phase 2 — hang-up prep ✅ (code only; live confirm = morning #4)

**Timeout:** `idle_timeout_secs` 1200 → 600 in `run_bot` (20 min of dead
air on an unattended call is money).

**Frame-path trace — `end_call` → Twilio REST hang-up (pipecat 1.2.1).**
Traced through the installed source; **the path is intact, no fix
needed.** Step by step:

1. `server.py end_call_handler` →
   `params.llm.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)`.
2. `EndTaskFrame` travels upstream to the task's source:
   `pipeline/task.py:838 _source_push_frame` → `:849 isinstance(frame,
   EndTaskFrame)` → `:852 await self.queue_frame(EndFrame(reason=...))`
   (default direction = DOWNSTREAM).
3. The `EndFrame` flows downstream through the pipeline to
   `FastAPIWebsocketOutputTransport`. On `EndFrame` the base output
   transport calls `stop(frame)`:
   `transports/websocket/fastapi.py:399 stop()` → `:406
   await self._write_frame(frame)` (then `:407 _client.disconnect()`).
4. `:490 _write_frame` → `:499 payload = await
   self._params.serializer.serialize(frame)`.
5. `serializers/twilio.py:129 serialize()` → `:142-147` guard
   `auto_hang_up (default True) and not _hangup_attempted and
   isinstance(frame, (EndFrame, CancelFrame))` → `:147 await
   self._hang_up_call()`.
6. `:179 _hang_up_call` → `:196` POST to
   `https://api.…twilio.com/2010-04-01/Accounts/{account_sid}/Calls/{call_sid}.json`
   with status=`completed`, `aiohttp.BasicAuth(account_sid, auth_token)`
   (`:199`) → Twilio terminates the call.

`server.py handle_call` constructs `TwilioFrameSerializer` with
`stream_sid`, `call_sid`, `account_sid`, `auth_token`, and
`auto_hang_up` defaults `True` — so every precondition the serializer's
`__init__` validates (twilio.py:84-96) is satisfied. The REST POST is
awaited *inside* the `stop()` sequence, before the WS disconnects, so
hang-up fires even if the human never hangs up. This closes the BACKLOG
(2026-05-27) "calls only ended when the human hung up" question at the
code level; **live confirmation is morning checklist #4.**

## Phase 3 — trust 🟡 (code complete + deterministically tested; LLM gates pending API credits)

**3.1 Evidence-grounded extraction.** `REPORT_TOOL.answers` is now
`key → {value, evidence}` (`additionalProperties` object schema,
`required: [value, evidence]`, evidence `type: [string, null]`).
Extraction system prompt: evidence MUST be a verbatim transcript quote,
null when no answer; hedged/uncertain replies are NOT a yes. Consumers
updated in the same change:
- `static/index.html`: renders each answer's evidence as a quoted muted
  line under the value row (handles old plain-value records too);
  non-`completed` terminal states render plainly ("No one answered the
  call." / busy / failed / error) with the click-to-dial CTA — this also
  closes the Phase 1 deviation; fallback CTA rule = `task_completed ==
  false` OR `confidence == low` OR non-`completed` terminal.
- `eval.py` judge prompt: compares `expected_answers[key]` against
  `answers[key].value`.

**3.2 Harness upgrades.** Programmatic checks (pure code, in
`run_scenario`, reported as `checks: [{name, pass, detail}]`):
`dtmf_silent` (no nonempty text blocks alongside a send_dtmf tool_use —
closes the open BACKLOG question), `evidence_grounded` (normalize both
sides: lowercase → strip non-`[a-z0-9 ]` → collapse spaces → substring),
`end_call_terminal` (no agent turns after `[ENDED CALL: …]`). New rubric
item `no_invented_answers`. New optional scenario field
`expected_result_checks` (plain-English assertions about the extracted
result), judge-graded via a `result_checks` object added to `JUDGE_TOOL`
(keyed `check_1…` in order). Overall scenario pass = rubric AND answers
AND checks AND result_checks (`summarize` returns the full breakdown).
Also: `eval.py` now calls `load_dotenv(<repo>/.env)` in `main()` — it
used to inherit dotenv loading from `import server`, which Phase 0
removed.

**3.3 New scenarios** (14 total now): `ambiguous_insurance` (hedged
answer → confidence not high, no unqualified yes), `refuses_pricing`
(no phone quotes → task_completed false, null price, low confidence —
canonical click-to-dial trigger), `partial_answers` (insurance answered
+ evidence; availability null; notes mention the gap), `wrong_number`
(pizza shop — no fabricated dental answers).

**3.4 Extraction fixtures** (`evals/extraction/`, 6): clean_yes,
clean_no, hedged_yes (plan's canonical example), refusal, partial,
multi_answer_price. Run via `eval.py --extraction`; comparison is
programmatic — answer keys matched by token overlap (extractor invents
key names), `values_match` normalizes bool/str/number (`120` ↔ `"$120"`),
`expected_confidence_any_of` lists, `evidence_grounded` on every result.
Per-fixture failures print inline; run JSON saved under `evals/runs/`.

**Gate status:**
- Deterministic: `pytest tests/` → **70 passed** (includes
  `test_eval_checks.py`: norm_text, all three checks, values_match,
  match_answer_key, summarize-overall logic).
- Plumbing: `eval.py --extraction` runs end-to-end — all 6 fixtures
  reach the API and fail only on the billing 400 (full TLS+auth round
  trip, request ids returned). `eval.py --list` shows all 14 scenarios.
- **⛔ Pending credits** (the only unfinished piece of Phases 0–6):
  `eval.py --extraction` must go 6/6 and the full suite 14/14. Protocol
  reminders for whoever runs it: a sim failure must reproduce twice
  before prompt surgery; cap full-suite runs at ~5.

## Phase 4 — MCP server ✅

**4.1 HTTP endpoints** (`server.py`):
- `POST /submit` now accepts **JSON or form**. JSON shapes: free-text
  `{phone, task}` (MCP path → `task_type`/`context` null) or `{phone,
  task_type, context}`. Branches on `Content-Type`; same normalization +
  502-on-create-failure as Phase 1. Form path (web form) unchanged in
  behavior.
- `GET /status/{call_sid}?wait=N` — long-poll. `wait` clamped 0–120
  (default 0). Loops: terminal status or `wait` elapsed → return full
  snapshot; else `asyncio.sleep(1)`. Snapshot replaces `transcript` with
  the live `ACTIVE_CALLS` observer's turns when the call is in-flight.
  404 only for unknown sid.
- `GET /calls?limit=N` — newest-first by `created_at`; each entry is
  `{call_sid, to_number, task, status, created_at, summary}` (summary =
  `result.summary` when present).

**4.2 `mcp_server.py`** — `FastMCP("DialAgent")` over stdio, thin httpx
client of the FastAPI app. Never imports `server`/`agent`. Env
`DIALAGENT_BASE_URL` (default `http://localhost:8000`) + `DIALAGENT_SECRET`
(sent as `X-DialAgent-Key`). Tools: `place_call(phone, task)` →
`POST /submit`; `get_call_status(call_sid, wait_seconds=60)` →
`GET /status`; `list_recent_calls(limit=10)` → `GET /calls`. Tool
docstrings written as host-LLM prompts (place_call warns it costs real
money / places a real call; get_call_status says poll until terminal).
`if __name__ == "__main__": mcp.run()` (stdio). Verified `FastMCP` API
against installed `mcp==1.27.2` (`run(transport='stdio')`,
`streamable_http_app()`→Starlette for Phase 6, `streamable_http_path`
configurable).

**4.3 `README.md`** — three sections in plan order: (1) remote connector
(stub heading; content lands Phase 6), (2) web form `?key=` URL, (3) run
your own instance (prereqs → `docs/stack-setup.md`, `.env` checklist,
`user_profile.json` from example, server + ngrok, stdio MCP JSON snippet
with abs-path `command`/`args` + `env`).

Test infra: `isolated_state` autouse fixture (fresh `DIALAGENT_CALLS_DIR`
tmp dir + cleared registries) moved to root `conftest.py`, shared by all
test files.

Gate evidence:
```
pytest tests/ -q
36 passed, 1 warning in 2.32s
```
(includes `test_mcp_endpoints.py`: JSON /submit both shapes, /status
terminal-immediate + long-poll-returns-terminal + partial-transcript +
404, /calls newest-first + limit, and the 3-tool registration check.)

## Phase 5 — form secret ✅

`DIALAGENT_SECRET` now guards the app. **Fail-fast at startup**: a
FastAPI `lifespan` calls `require_env("DIALAGENT_SECRET")` (raises if
unset). `verify_key` dependency accepts the secret via `X-DialAgent-Key`
header **or** `?key=` query param (EventSource can't set headers),
constant-time compare (`hmac.compare_digest`), 401 otherwise.

- **Protected** (`Depends(verify_key)`): `/submit`, `/status/*`,
  `/calls`, `/result/*`, `/events/*`.
- **Open**: `/` (the page), `/ws` (Twilio media), `/call-status` (Twilio
  can't send our header; X-Twilio-Signature validation deferred per plan
  out-of-scope).

`static/index.html` reads `key` from the URL query and appends it to
every `/submit` / `/events` / `/result` request via `withKey()`; with no
key it disables the form and shows "Append ?key=<your secret>".
`docs/stack-setup.md` checklist updated with `DIALAGENT_SECRET` + how to
generate it. Test infra: `isolated_state` fixture sets
`DIALAGENT_SECRET=test-secret`; both `asgi_client()` helpers send it
(read from env) so prior tests stay green; `test_auth.py` uses a no-key
client to exercise the boundary.

Gate evidence:
```
pytest tests/ -q
44 passed, 1 warning in 2.32s
```
(`test_auth.py`: 401 without key / with wrong key; 200 with header and
with `?key=`; `/submit` 200 with key; `/call-status` keyless → 204; `/`
open; lifespan raises `RuntimeError` when the secret is unset.)

## Phase 6 — remote connector ✅

The FastMCP streamable-HTTP app is mounted into the FastAPI app at
`/connector/<DIALAGENT_SECRET>` (MCP endpoint `…/connector/<secret>/mcp`).
The secret lives in the path because claude.ai / ChatGPT no-auth
connectors can't send custom headers; a wrong secret matches no mount and
404s.

Implementation (`server.py`): `import mcp_server`; `_mcp_app =
mcp_server.mcp.streamable_http_app()` at import (this also creates the
session manager); the parent `lifespan` runs `async with
mcp_server.mcp.session_manager.run():` (a mounted sub-app's own lifespan
never fires — this is the documented SDK pattern); `app.mount(CONNECTOR_PATH,
_mcp_app)` after the API routes. `CONNECTOR_PATH` is derived from
`DIALAGENT_SECRET` at import and exposed for tests. No import cycle:
`server` → `mcp_server`; `mcp_server` imports only `mcp`/`httpx`/`os`. One
tool definition, two transports (stdio `__main__` + mounted HTTP).

`mcp_server.py` config: `FastMCP("DialAgent", json_response=True,
transport_security=TransportSecuritySettings(enable_dns_rebinding_protection
=False))`. **json_response** → JSON bodies (tools are request/response;
simpler than SSE). **DNS-rebinding protection off** — the connector is
served behind an arbitrary ngrok `Host` header; with it on (the default),
the MCP transport returns **421 Misdirected Request** and the connector
breaks. The secret-in-path is the guard. (Caught via the handshake probe;
would have failed the real claude.ai connection in morning #7.)

README now leads with connector setup: claude.ai (Settings → Connectors →
Add custom connector → URL; web/desktop/mobile), ChatGPT (developer mode;
Plus/Pro), Gemini → CLI/stdio only. Caveats recorded (laptop awake +
ngrok; calls on operator keys; rotate secret to revoke; reserved ngrok
domain keeps the URL stable).

Gate evidence:
```
pytest tests/ -q
45 passed, 1 warning in 2.16s
```
`test_connector_handshake_and_tools_list`: real JSON-RPC over ASGI inside
`session_manager.run()` — initialize → 200 (`mcp-session-id` +
serverInfo "DialAgent"), initialized → 202, tools/list → 3 tools,
`/connector/WRONG/mcp` → 404.

## E. Morning checklist (needs Jay — do NOT run unattended)

The 7 real-call rows in `docs/mvp-plan.md` § E. Telephony verification
is the only part that requires a human + live Twilio. Handed off.
