# MVP progress — morning handoff

Per-phase status for the MVP build (`docs/mvp-plan.md`). Updated as each
phase's gate passes. Unattended rules honored throughout: **no real
phone calls**, work on `main`, one commit per phase, `.env` append-only.

Legend: ✅ done · 🚧 in progress · ⛔ blocked · ⏭️ skipped

| Phase | What | Status | Gate evidence |
|---|---|---|---|
| 0 | Foundations: `agent.py` split, requirements fix | ✅ (1 sub-gate deferred) | imports clean w/o env; fresh `/tmp` venv install + import OK |
| 1 | Call lifecycle | 🚧 | — |
| 2 | Hang-up prep (code only) | — | — |
| 3 | Trust: evidence + confidence + evals | — | — |
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

## Phase 1 — call lifecycle

_Next._

## Phase 2 — hang-up prep

## Phase 3 — trust

## Phase 4 — MCP server

## Phase 5 — form secret

## Phase 6 — remote connector

## E. Morning checklist (needs Jay — do NOT run unattended)

The 7 real-call rows in `docs/mvp-plan.md` § E. Telephony verification
is the only part that requires a human + live Twilio. Handed off.
