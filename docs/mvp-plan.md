# MVP plan — features and evals

Implementation spec for finishing v0.5 to a usable MVP. Written for the
implementing session: each phase is runnable end-to-end before moving to
the next, and each feature lands **with** its evals/checks in the same
phase — not in a cleanup pass later.

**Definition of usable**: a friend submits a task and every call reaches
a terminal state they can see and trust within ~3 minutes — *including
calls that never connect*. The hard parts (IVR, voicemail, extraction,
guardrails, eval loop) are done. What's missing is lifecycle plumbing,
trust signals, and the MCP surface.

---

## Execution protocol — implementing session, read first

This plan runs unattended (Jay is asleep). Hard rules:

1. **No real phone calls.** Never run `place_call.py`, never call the
   Twilio REST API. In tests, always monkeypatch
   `server.place_twilio_call` and `agent.extract_result` is mocked
   wherever extraction isn't the thing under test.
2. **Allowed outbound network**: Anthropic API (evals/extraction) and
   pip installs. Nothing else. Don't start ngrok — nothing tonight
   needs public exposure.
3. **Git**: work directly on `main`. If the plan docs are uncommitted
   at start, commit them first ("Add MVP plan"). One commit per phase,
   after its gate passes, terse message ("Phase 1: call lifecycle +
   status callback"). Never push. Before the first commit, verify
   `.gitignore` covers `.env`, `user_profile.json`, `calls/`,
   `evals/runs/` — fix if not. Never commit any of those.
4. **`.env`**: append new vars only (`DIALAGENT_SECRET`); never edit or
   log existing values.
5. **Progress file**: maintain `docs/mvp-progress.md` — per phase:
   status (done / blocked / skipped), gate evidence (test output
   snippet), and any deviation from this spec with one line of why.
   This is the morning handoff; keep it current as you go.
6. **Failure protocol**: a gate still failing after 2 genuine fix
   attempts = blocker. Document it, park the WIP on a `wip/<phase>`
   branch (keep `main` green), and continue with phases that don't
   depend on it. Dependency graph: 0 → 1 → 4 → 5 → 6; 3 depends only
   on 0; 2 depends on nothing.
7. **Eval token discipline**: while iterating, run single scenarios
   (`eval.py <name>`); run the full suite once at the end of Phase 3
   and once after Phase 4. A sim failure must reproduce twice before
   you change prompts over it. Cap full-suite runs at ~5 for the night.
8. **Venv**: develop in the existing `.venv`. The fresh-install gate
   (0.2) uses a scratch venv under `/tmp` (same Python version as
   `.venv/bin/python --version`), deleted afterwards. Never delete or
   rebuild `.venv` itself.

### Decisions already made — do not relitigate

- Deterministic tests: **pytest**, in `tests/`. Add `pytest` to
  `requirements.txt` (single requirements file is fine at this size).
- Tool-call transcript logging happens **in the function handlers** in
  `run_bot` (they already have `digits`/`reason` in scope) — not by
  observing Pipecat function-call frames.
- Long-poll semantics: **block until terminal status or timeout, then
  return the full current snapshot**. No new-turn detection, no cursors.
- Evidence shape: `answers[key] = {value, evidence}`, evidence verbatim
  quote or null.
- Twilio `canceled` maps to `failed`.
- `DIALAGENT_SECRET` is required at server startup (fail fast if
  unset). `/call-status` and `/ws` stay unauthenticated.
- `mcp_server.py` is a thin HTTP client (httpx) of the FastAPI app. It
  never imports `server` or `agent`.
- `CALLS_DIR` becomes env-overridable (`DIALAGENT_CALLS_DIR`) so tests
  can point it at a tmp dir.
- Existing scenario YAMLs don't change for the evidence migration; the
  judge and fixture runner compare expected values against
  `answers[key].value`.
- `calls/` is currently empty — no record migration anywhere.
- **Primary distribution is the remote connector to Jay's instance**
  (Jay, 2026-06-12: "forget about BYOK"). The stdio entrypoint and
  self-host docs stay as secondary paths, not the product motion.
- Connector auth = secret embedded in the URL path. OAuth deferred to
  hosted deployment.

---

## Gaps this plan closes

1. **No-answer black hole.** If the office never picks up, Twilio never
   opens the WS, no record is written, `/result` 404s forever, the SSE
   spinner spins forever. Worst current bug — a friend hits it on call #2.
2. **No MCP server.** The primary distribution surface doesn't exist.
3. **Thin, late call records.** No timestamp/phone/status/duration;
   nothing on disk until the call ends; tool calls absent from transcript.
4. **No trust signals.** `confidence` is extracted but nothing consumes
   it; answers carry no evidence; click-to-dial fallback never triggers.
5. **Hang-up unverified.** Backlog (2026-05-27) says calls only ended
   when the human hung up. Code now pushes `EndTaskFrame` and the
   serializer has account creds — unverified against a real call.
6. **Import coupling.** `server.py` has import-time side effects
   (profile load, dotenv, logger); `eval.py` already eats them and
   `mcp_server.py` would be the third consumer.
7. **`requirements.txt` doesn't reproduce.** `anthropic` and `loguru`
   are imported but not pinned; `pipecat-ai` is pinned without extras.
8. **`/submit` is exposed via ngrok unauthenticated.** Anyone with the
   URL can place calls on Jay's Twilio account.
9. **No README.** The done-bar is "a friend installs this"; there is
   nothing to hand a friend.

---

## Build order

All gates below are deterministic and run unattended — no real calls.
All real-call verification is batched into the **morning checklist**
(section E), which Jay runs when awake.

| Phase | What | Unattended gate |
|---|---|---|
| 0 | Foundations: `agent.py` split, requirements fix | clean imports; fresh scratch-venv install works |
| 1 | Call lifecycle: record schema v2, status callback, live registry, tool-call logging | `tests/test_lifecycle.py` + `tests/test_observer.py` green |
| 2 | Hang-up prep (code only) | code-trace written to progress file; timeout lowered |
| 3 | Trust: evidence + confidence + eval upgrades | full eval suite green (sims + fixtures + checks) |
| 4 | MCP server + endpoints + README | `tests/test_mcp_endpoints.py` green |
| 5 | Form secret | auth tests green |
| 6 | Remote connector surface (claude.ai + ChatGPT) | mounted `/connector` MCP endpoint tests green |
| — | Morning checklist (Jay) | real-call verification, then the friend test |

---

## Phase 0 — foundations

### 0.1 Split `agent.py` out of `server.py`

`agent.py` gets, with **no import-time side effects** (no dotenv, no
logger config, no profile load, no env reads at module level):

- `SYSTEM_PROMPT_TEMPLATE`, `render_system_prompt`, `format_profile_block`
- `END_CALL_SCHEMA`, `SEND_DTMF_SCHEMA`, `REPORT_TOOL`
- `TASK_TEMPLATES`
- `extract_result`, `save_call_record`, `require_env`
- `get_user_profile()` — lazy, module-cached (`_profile = None`)
  replacement for the current module-level `USER_PROFILE`. Callers:
  `run_bot` (server) and `run_scenario` (eval). `eval.py:132`
  references `server.USER_PROFILE` today — update it.
- Constants: `CALL_MODEL` / `EXTRACT_MODEL` (both currently
  `claude-haiku-4-5-20251001`, hardcoded in two places in `server.py`),
  `EST_COST_PER_MIN = 0.05`, `CALLS_DIR` (resolved as
  `Path(os.environ.get("DIALAGENT_CALLS_DIR", default))` — read env
  **inside a function or at first use**, not at import).

`server.py` keeps: FastAPI app, Pipecat pipeline, Twilio, SSE,
`TurnLogObserver`, `load_dotenv` (it's the entrypoint) — but change
`load_dotenv(override=True)` to `load_dotenv()`. The `override=True`
clobbers env vars set by tests; nothing depends on it.

`eval.py` switches imports from `server` to `agent` (it must no longer
import `server` at all — that's the gate). `eval.py` keeps its own
`JUDGE_MODEL` and the sim/receptionist model constant.

`place_call.py`: import the shared TwiML construction instead of
duplicating, or leave untouched — do not spend time on it.

**Gate**: in the existing venv, with `user_profile.json` and `.env`
temporarily renamed: `python -c "import agent"` succeeds and
`python -c "import eval"` succeeds (rename back after). Eval harness
runs a single scenario successfully.

### 0.2 Reproducible install + housekeeping

- Get exact pins from the known-good env: `pip freeze` in `.venv`,
  take the entries for `anthropic`, `loguru`, and anything else
  `server.py`/`eval.py` import directly that's missing from
  `requirements.txt`. Add `pytest`, `mcp`, `httpx` (Phase 4 needs the
  latter two; pin from pip's resolution).
- pipecat extras: don't guess names — read the installed package's
  declared extras (`importlib.metadata.metadata("pipecat-ai")` or its
  `METADATA` file) and pick the ones covering anthropic/deepgram/
  silero/websocket transport.
- Fix the Deepgram TTS deprecation:
  `DeepgramTTSService(settings=DeepgramTTSService.Settings(voice="aura-2-thalia-en"))`.

**Gate**: scratch venv under `/tmp` (same Python version), `pip install
-r requirements.txt`, then `python -c "import server, agent, eval"`
succeeds with stub env (`.env` absent is fine since 0.1). Delete the
scratch venv.

---

## Phase 1 — call lifecycle

### 1.1 Record schema v2

```json
{
  "call_sid": "CA...",
  "to_number": "+1...",
  "task": "Ask if they accept Delta Dental PPO insurance.",
  "task_type": "plan_acceptance",
  "context": "Delta Dental PPO",
  "status": "completed",
  "created_at": "2026-06-12T14:03:11+00:00",
  "ended_at": "2026-06-12T14:05:20+00:00",
  "duration_s": 129,
  "est_cost_usd": 0.11,
  "transcript": [...],
  "result": {...},
  "error": null
}
```

- `status` enum: `dialing | in_progress | extracting | completed |
  no_answer | busy | failed | error`. `task_type`/`context` are null
  for free-text tasks (MCP path). `duration_s`/`ended_at` null until
  terminal. Timestamps: `datetime.now(timezone.utc).isoformat()`.
- **Stub record at submit**: order is `calls.create` → write stub
  (`status: "dialing"`) → create `LIVE_EVENTS` queue. If `create`
  raises, return 502, no record.
- Status transitions are **persisted to disk** when they happen:
  `in_progress` at WS connect, `extracting` when the pipeline ends,
  terminal at final save. Read-modify-write the stub via
  `save_call_record` (it's already atomic).
- `duration_s`: prefer Twilio's `CallDuration` (arrives on the
  `completed` callback, as a string of seconds); fall back to
  `ended_at - created_at`. `est_cost_usd = round(duration_s / 60 *
  EST_COST_PER_MIN, 2)`. (This is also all the Stripe prep that's
  justified now — you can't bill later for usage you never metered.)
- **Phone normalization in `/submit`**: strip spaces, dashes, dots,
  parens; 10 digits → prepend `+1`; 11 digits starting with 1 → prepend
  `+`; result must match `^\+\d{8,15}$` else 400. Friends will paste
  `(415) 555-1234`.

### 1.2 Crash-proof record save in `run_bot`

Wrap everything after pipeline start in try/finally: on any exception,
save the record with `status: "error"` and the exception string; on
success, `status: "completed"`. Always push `None` to the live queue
and remove the registry entry in `finally`. This makes the WS path
self-terminalizing, which is what keeps the callback handler simple
(next item).

### 1.3 Twilio status callback — kill the black hole

`calls.create(..., status_callback=f"{NGROK_URL}/call-status",
status_callback_event=["completed"], status_callback_method="POST")`.

Twilio POSTs **form-encoded**: `CallSid`, `CallStatus` ∈ `completed |
busy | no-answer | failed | canceled`, `CallDuration` (string, only
meaningful on completed). New endpoint `POST /call-status`:

- Load the record by `CallSid`. Unknown sid or already-terminal →
  return 204, do nothing (idempotent).
- Record still `dialing` (WS never connected):
  - `no-answer` → `no_answer`; `busy` → `busy`; `failed`/`canceled` →
    `failed`; `completed` → `error` with
    `error: "call completed but media stream never connected"`.
  - Set `ended_at`, `duration_s` (from `CallDuration` if present),
    `est_cost_usd`; push `None` to the live queue.
- Record `in_progress`/`extracting`: the WS path owns it (1.2
  guarantees it terminalizes). The callback only backfills
  `duration_s`/`est_cost_usd` from `CallDuration` if the record lacks
  them, and never touches `status`. **Do not** write `error` here —
  the callback typically arrives while extraction is still running.
- Known limitation (accept it): a hard process crash mid-call leaves
  `in_progress` forever. Fine for v0.5.

UI: results page renders non-`completed` terminal states plainly ("No
one answered") with the click-to-dial `tel:` link as the CTA.

### 1.4 Live state out of the SSE queue

`ACTIVE_CALLS: dict[str, TurnLogObserver]` in `server.py`, registered
at WS connect (keyed by `call_data["call_id"]`), removed in `run_bot`'s
`finally`. SSE keeps `LIVE_EVENTS` as-is; the registry is the source of
truth the Phase-4 status endpoint reads partial transcripts from.

### 1.5 Tool calls in the transcript

Add `TurnLogObserver.log_tool(text)`: appends
`{"role": "agent", "text": text}` to `self.turns` and emits to the live
queue. Call it from the handlers in `run_bot`:
`send_dtmf_handler` → `[PRESSED: <digits>]`, `end_call_handler` →
`[ENDED CALL: <reason>]`. Define `observer` before the handlers in
`run_bot`. (Matches the eval-transcript format exactly. Do not go
spelunking Pipecat function-call frames — the handlers already have
the data.)

**Gate — `tests/test_lifecycle.py` + `tests/test_observer.py`**
(pytest, httpx `ASGITransport` against the app; monkeypatch
`server.place_twilio_call` → `"CAtest123"`; `DIALAGENT_CALLS_DIR` →
tmp dir; set `DIALAGENT_SECRET` once Phase 5 lands):

- `/submit` (form and, after Phase 4, JSON) → stub record on disk with
  all v2 fields, `status: dialing`.
- Phone normalization: `(415) 555-1234` → `+14155551234`; garbage → 400.
- `/call-status` with `CallStatus=no-answer` → record `no_answer`,
  `ended_at` set, live queue got `None`, `/result` returns the terminal
  record. Repeat for `busy`, `failed`, `canceled`→`failed`, and
  `completed`-while-`dialing` → `error`.
- Callback on an already-terminal record → 204, record unchanged.
- `TurnLogObserver.log_tool` → turn appended + queued.

---

## Phase 2 — hang-up prep (code only; live test deferred)

The open question — when the agent invokes `end_call`, does Twilio
actually disconnect? — is only answerable with a live call, so the
*answer* moves to the morning checklist. Unattended work:

- Read pipecat 1.2.1's installed source: trace `EndTaskFrame`
  (pushed upstream by `end_call_handler`) → `PipelineTask` → `EndFrame`
  downstream → `TwilioFrameSerializer` REST hang-up (`auto_hang_up`
  behavior; the serializer already gets `account_sid`/`auth_token` in
  `server.py`). Write the trace (frame path + the exact line that fires
  the REST call, or the gap found) into `docs/mvp-progress.md`. If the
  path is plainly broken, fix it now so the morning test passes first
  try.
- Drop `idle_timeout_secs` from 1200 to 600 — 20 minutes of dead air on
  an unattended call is money.

**Gate**: trace written up; timeout change in. Live confirmation →
morning checklist #4.

---

## Phase 3 — trust: evidence + confidence

### 3.1 Evidence-grounded extraction

`REPORT_TOOL.answers` changes from `key → value` to:

```json
"answers": {
  "takes_delta_dental_ppo": {
    "value": true,
    "evidence": "Yes, we're in-network with Delta Dental PPO."
  }
}
```

Schema: `additionalProperties: {type: object, properties: {value: {},
evidence: {type: ["string", "null"]}}, required: [value, evidence]}`.
Extraction prompt: evidence MUST be a verbatim quote from the
transcript; `null` when no answer was obtained — never paraphrase,
never invent.

Consumers to update in the same change:
- `static/index.html` (read it first — 328 lines): render value +
  evidence as a quoted line under each answer.
- Fallback CTA rule in the results view: `confidence == "low"` OR
  `task_completed == false` OR status is a non-`completed` terminal →
  lead with "We couldn't get a clear answer — here's what we heard"
  and the `tel:` link as primary CTA.
- `eval.py` judge prompt: compare each `expected_answers` field against
  `answers[key].value`.

### 3.2 Eval harness upgrades (`eval.py`)

**Programmatic checks** — pure code, no LLM, run inside `run_scenario`,
reported in the run JSON as `"checks": [{name, pass, detail}]`; overall
scenario pass requires rubric AND answers AND checks:

1. `dtmf_silent` — any response containing a `send_dtmf` tool_use has
   no nonempty text blocks. (Closes the open BACKLOG question.)
2. `evidence_grounded` — every non-null `evidence` appears in the
   transcript after normalizing both sides: lowercase, strip every char
   not in `[a-z0-9 ]`, collapse runs of spaces, then substring test.
3. `end_call_terminal` — no agent turns after `[ENDED CALL: ...]`.

**New rubric item**: `no_invented_answers` — "If the receptionist could
not or would not provide the requested info, did the agent avoid
assuming or inventing an answer? (n/a if all info was provided.)"

**New optional scenario field** `expected_result_checks`: list of
plain-English assertions about the *extracted result*, judge-graded
pass/fail via a `result_checks` object added to `JUDGE_TOOL` (same
shape as `rubric`). Counts toward overall pass.

### 3.3 New scenarios (`evals/scenarios/`)

1. `ambiguous_insurance.yaml` — receptionist: "I *think* we take Delta,
   but Maria handles insurance and she's out — call back after 2."
   `expected_result_checks`: confidence is not "high"; the insurance
   answer's value is not an unqualified yes (null or hedged); evidence
   reflects the hedge.
2. `refuses_pricing.yaml` — "We don't quote prices over the phone."
   Checks: `task_completed == false`, price value null, confidence low.
   Canonical click-to-dial trigger.
3. `partial_answers.yaml` — two questions, one answered ("yes we take
   Delta"), one not ("scheduling desk is gone for the day"). Checks:
   answered field has value + evidence; unanswered is null; confidence
   not "high"; notes mention the gap.
4. `wrong_number.yaml` — persona is a pizza shop. Agent recognizes the
   mismatch, apologizes, ends within a few turns. Checks:
   `task_completed == false`, no dental answers fabricated.

Existing 10 scenarios must keep passing — the schema change touches the
judge prompt, so the phase ends with one full-suite run.

### 3.4 Extraction fixtures (`evals/extraction/`)

Canned-transcript regression tests for the extractor alone — fast,
cheap, isolate extractor changes from agent changes. Run via
`eval.py --extraction`:

```yaml
name: hedged_yes
task: Ask if they accept Delta Dental PPO insurance.
transcript:
  - {role: user, text: "Bay Dental, this is Amy."}
  - {role: agent, text: "Hi, calling on behalf of Jay — do you take Delta Dental PPO?"}
  - {role: user, text: "I believe so, but our office manager confirms insurance and she's out today."}
expected_answers:
  takes_delta_dental_ppo: null
expected_confidence_any_of: [low, medium]
```

Comparison is **programmatic, not judged**: normalize both sides
(booleans: `true/yes` ↔ `True`; strings: lowercase + strip; null ↔
null) and pick fixture expected-values that compare unambiguously.
`expected_confidence_any_of` is a list — never a single enum value;
exact-match on a judgment call is a flaky gate. Run `evidence_grounded`
on every fixture result. Seed with 6: clean yes, clean no, hedged yes,
refusal, partial, multi-answer with a price.

**Gate (Phase 3)**: `eval.py --extraction` 6/6; full suite green — 14
scenarios (10 existing + 4 new), all programmatic checks passing. Sims
are nondeterministic: a failure must reproduce twice before prompt
surgery (protocol rule 7).

---

## Phase 4 — MCP server

### 4.1 HTTP endpoints in `server.py` to back it

- `POST /submit` — accept JSON as well as form data:
  `{"phone", "task"}` (free-text) or `{"phone", "task_type",
  "context"}`. Same normalization/validation; returns
  `{call_sid, task}`.
- `GET /status/{call_sid}?wait=60` — **long-poll**: `wait` clamped to
  0–120, default 0. Loop: if record status is terminal, return now;
  else `asyncio.sleep(1)` and re-check until `wait` elapses. Response
  is always the full current snapshot: the record, with `transcript`
  replaced by the registry observer's live turns when the call is
  active. 404 only for unknown sid.
- `GET /calls?limit=10` — newest-first by `created_at`:
  `[{call_sid, to_number, task, status, created_at, summary}]` where
  `summary` is `result.summary` when present.

### 4.2 `mcp_server.py`

`FastMCP` over **stdio** (SDK: `mcp.server.fastmcp`), thin httpx client
of the FastAPI app. Env: `DIALAGENT_BASE_URL` (default
`http://localhost:8000`), `DIALAGENT_SECRET` (sent as
`X-DialAgent-Key`). Never imports `server` or `agent` — live state
lives in the server process; two processes, one source of truth.

| Tool | Maps to | Notes |
|---|---|---|
| `place_call(phone, task)` | `POST /submit` | returns `call_sid` immediately |
| `get_call_status(call_sid, wait_seconds=60)` | `GET /status/...` | long-poll |
| `list_recent_calls(limit=10)` | `GET /calls` | per BACKLOG |

Tool descriptions are prompts for the host LLM — write them as such.
`get_call_status`: "Calls take 1–3 minutes. Call this repeatedly with
wait_seconds=60 until status is terminal (completed / no_answer / busy
/ failed / error)." `place_call`: state that it costs real money and
places a real call, so hosts don't invoke it speculatively.

### 4.3 README.md (new — required for the friend test)

One page, three sections, in this order:
1. **Use DialAgent** — connector URLs for claude.ai and ChatGPT
   (content lands in Phase 6; leave a stub heading until then).
2. **Web form** — the `?key=` URL, what it does.
3. **Run your own instance** (operators/self-hosters, not the user
   path): prerequisites, `.env` checklist (point at
   `docs/stack-setup.md`), `user_profile.json` from the example, run
   server + ngrok, and the stdio MCP JSON snippet for Claude Desktop /
   Cursor / Gemini CLI (`command: <abs path>/.venv/bin/python`,
   `args: ["mcp_server.py"]`,
   `env: {DIALAGENT_SECRET, DIALAGENT_BASE_URL}`).

**Gate — `tests/test_mcp_endpoints.py`** (ASGITransport, fixture
records in a tmp `DIALAGENT_CALLS_DIR`, monkeypatched
`place_twilio_call`):

- JSON `/submit` (both shapes) → stub record, `call_sid` returned.
- `/status` on a terminal record returns immediately, full record.
- `/status?wait=5` on a fake in-progress call (inject a registry entry;
  background task appends a turn then writes a terminal record) →
  returns the terminal snapshot before the timeout; partial-transcript
  path returns observer turns.
- `/calls` newest-first, respects `limit`.
- Unknown sid → 404.

MCP layer itself is thin enough that testing the endpoints covers the
logic; do not build an MCP-protocol test rig.

---

## Phase 5 — form secret

- `DIALAGENT_SECRET` env var, **required at startup** (fail fast).
  Generate via `python -c "import secrets; print(secrets.token_urlsafe(16))"`,
  append to `.env`, add to the checklist in `docs/stack-setup.md`.
- FastAPI dependency `verify_key`: accept `X-DialAgent-Key` header or
  `?key=` query param; constant-time compare; 401 otherwise.
- Protected: `/submit`, `/status/*`, `/calls`, `/result/*`, `/events/*`.
  Open: `/` (the page itself), `/ws` (Twilio media), `/call-status`
  (Twilio can't send our header; sid-guessing risk accepted for v0.5 —
  X-Twilio-Signature validation is deferred, see out of scope).
- `static/index.html`: read `key` from the URL query, include it in
  every fetch/SSE URL; if absent, show "append ?key=... to the URL".
  Jay's phone bookmark is `https://<ngrok>/?key=<secret>`.

**Gate**: tests — protected endpoints 401 without key, 200 with header
and with query param; `/call-status` works keyless.

---

## Phase 6 — remote connector surface (claude.ai + ChatGPT)

**This is the primary distribution surface** (decision 2026-06-12:
BYOK de-emphasized — people use DialAgent by connecting to Jay's
running instance, zero install). The laptop is already publicly
tunneled for Twilio; the same ngrok hostname serves the connector.

- Mount the FastMCP server from `mcp_server.py` into the FastAPI app
  over **streamable HTTP** at `/connector/{DIALAGENT_SECRET}`. The
  path carries the secret because claude.ai / ChatGPT custom
  connectors can't send custom headers in no-auth mode; OAuth is
  post-MVP. Follow the installed `mcp` SDK's documented pattern for
  mounting `streamable_http_app()` into an existing Starlette/FastAPI
  app — the session manager must run inside the parent app's
  lifespan. Read the installed SDK's example; don't guess the API.
- No import cycle: `server.py` imports `mcp_server.py`;
  `mcp_server.py` imports only `mcp`/`httpx`/`os`. Tools keep calling
  the HTTP endpoints via `DIALAGENT_BASE_URL` (self-calls when
  mounted) — one tool definition, two transports. The stdio
  entrypoint (`if __name__ == "__main__"`) stays for Claude Desktop /
  Cursor / Gemini CLI users.
- ngrok: use a reserved/static domain so the connector URL survives
  restarts — document in README.
- README leads with connector setup: "Add to claude.ai" (Settings →
  Connectors → Add custom connector → the URL; works on web, desktop,
  and mobile — connections come from Anthropic's cloud) and "Add to
  ChatGPT" (Settings → Apps → Advanced settings → Developer mode →
  add connector; Plus/Pro required). Consumer Gemini app does NOT
  support custom MCP connectors — Gemini users go through Gemini CLI
  (stdio config, secondary README section).
- Caveats to record in README: the laptop must be awake with server +
  ngrok running; every connected friend's calls run on Jay's
  Twilio/Deepgram/Anthropic keys; anyone with the URL can place calls
  (rotate `DIALAGENT_SECRET` to revoke).

**Gate — extend `tests/test_mcp_endpoints.py`**: the mounted connector
path answers an MCP `initialize` handshake and `tools/list` returns
the 3 tools; a wrong-secret path 404s. Raw JSON-RPC POSTs over the
ASGI transport are enough — no MCP-protocol test rig.

---

## Evals — summary of the three layers

Defined per-feature above; consolidated view: LLM-judged scenarios for
agent/extraction *behavior* (Phase 3), programmatic checks for
*invariants* (Phases 1, 3, 4, 5 — pytest + in-harness checks), real
calls for *telephony* (morning checklist only). The conversation-sim
harness can't see lifecycle plumbing; don't pretend it can.

### E. Morning checklist — real-call verification (needs Jay)

The only part of this plan that requires a human and live telephony.
~15 minutes, real Twilio. Prep: pull `main`, start server + ngrok per
README, confirm `.env` has `DIALAGENT_SECRET`. Each row checks the
saved record, the web UI, and (where relevant) MCP output.
Implementing session: do NOT run these — this section is the handoff.

| # | Call | Expected terminal state |
|---|---|---|
| 1 | Jay's cell, answer, give clean answers | `completed`, full v2 record, evidence quotes verbatim, duration + cost set |
| 2 | Jay's cell, don't answer | `no_answer` within ~60s, SSE closed, fallback CTA shown |
| 3 | Jay's cell, decline the call | `busy`/`failed` terminal record |
| 4 | Jay's cell, let it hit voicemail | agent invokes `end_call("voicemail")`, Twilio disconnects with no human hang-up (closes Phase 2) |
| 5 | Jay's cell, answer, refuse to answer the question | `completed`, `task_completed: false`, low confidence, fallback CTA leads |
| 6 | From Claude Desktop via stdio MCP: real dental office, plan-acceptance question | structured answer with evidence lands in chat in ≤3 min |
| 7 | Add the connector in claude.ai (Settings → Connectors) and ChatGPT (developer mode), per README; run one real task from each | tools listed in both hosts; call placed; answer renders in chat (closes Phase 6) |

---

## Out of scope (decided, not forgotten)

Batch calls, Stripe/monetization, hosted HTTP+SSE MCP, accounts beyond
the shared secret, PII tasks, Mode 2, any vertical but dental. Plus,
explicitly decided against for this plan:

- **Call audio recording** (Twilio `record=True`) — transcript +
  evidence quotes are the v0.5 trust mechanism; audio adds storage and
  retrieval work with no consumer yet.
- **X-Twilio-Signature validation** on `/call-status` and `/ws` —
  revisit at hosted deployment.
- **Retries on failed calls** — the host/user re-invokes; no auto-redial.
- **Latency tuning** (BACKLOG item) — revisit only if morning smoke
  calls feel laggy.
- A hard mid-call process crash leaving `in_progress` — acceptable.

## Done when

- **Unattended-done**: Phases 0–6 gates pass; full eval suite green
  (14 scenarios + 6 extraction fixtures + programmatic checks); pytest
  suite green; `docs/mvp-progress.md` records evidence per phase.
  Implementation stops here and hands off.
- **Actually done**: Jay runs the morning checklist and all 7 rows pass.
- The v0.5 bar: a friend pastes the connector URL into claude.ai or
  ChatGPT — no install, no keys — asks it to call a dentist, and gets
  a trustworthy answer (or an honest failure with a click-to-dial)
  within ~3 minutes.
