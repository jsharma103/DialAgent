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
| 3 | Trust: evidence + confidence + evals | 🚧 | — |
| 4 | MCP server + endpoints + README | — | — |
| 5 | Form secret | — | — |
| 6 | Remote connector surface | — | — |
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

3. **⛔ No credentials anywhere.** No `.env` file, and `ANTHROPIC_API_KEY`
   / `DEEPGRAM_API_KEY` / all `TWILIO_*` / `NGROK_URL` are unset in the
   shell. Impact: **every gate that calls the Anthropic API cannot run
   unattended.** Specifically blocked:
   - Phase 0.1 sub-gate "eval runs a single scenario"
   - **Phase 3 gate** (full eval suite: sims + extraction fixtures)
   - Phase 3.4 extraction fixtures
   All *deterministic* gates (Phases 1, 2, 4, 5, 6 — pytest with
   monkeypatched `place_twilio_call` and mocked `extract_result`) are
   **unaffected** and proceed normally.
   **Action for Jay:** drop a real `.env` in the project root (or
   `export ANTHROPIC_API_KEY=...`) and I can run the deferred eval gates.
   `user_profile.json` also missing — copy from `user_profile.example.json`.

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

## Phase 3 — trust

## Phase 4 — MCP server

## Phase 5 — form secret

## Phase 6 — remote connector

## E. Morning checklist (needs Jay — do NOT run unattended)

The 7 real-call rows in `docs/mvp-plan.md` § E. Telephony verification
is the only part that requires a human + live Twilio. Handed off.
