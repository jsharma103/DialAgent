# Backlog

Polish items and follow-ups that came up during v0 development. Pull
from here when the current phase is solid and you want to sharpen things
before moving on.

---

## MCP server — implementation prep notes

Notes for when building `mcp_server.py` (per the "Distribution shape
(MCP-first)" section in `docs/v0.5-shopping-concierge.md`). None are
blocking for v0.5; flag them when the build starts.

**Add `list_recent_calls(limit=10)` as a third MCP tool.** ~15 lines.
Returns the last N saved records from `calls/`. Useful in v0.5 (skip
having to `ls calls/` to find a sid) and load-bearing later when mobile
orchestrators want to surface "here's what you've done." Cheap. Include
in the initial MCP build alongside `place_call` and `get_call_status`.

**API-key auth — defer to the HTTP+SSE transition.** Stdio MCP runs
locally with no network surface, so accept-and-ignore-auth-headers is
YAGNI today. When adding HTTP+SSE for hosted deployment:
- `Authorization: Bearer dlk_xxx` header parsing on tool calls and
  any FastAPI endpoints that go remote
- A `users` table (or equivalent) mapping keys → user IDs
- Balance / rate-limit check before placing calls
- Stripe webhook to credit balances on payment
Doing it earlier buys nothing — same code change either way.

**Files**: `mcp_server.py` (new), `server.py` (auth additions when
hosted).

---

## Latency: tune end-of-turn detection for terse answers

**Observed**: Phase 3 Scenario B (restaurant), 2026-05-27. User said
just "No." — agent took **~4.6s** to respond, vs ~1.2s on longer
replies.

**Root cause**: three layers of end-of-turn detection stack up on short
utterances:
1. `SileroVADAnalyzer` silence threshold
2. Deepgram STT endpointing / `utterance_end_ms`
3. `LLMUserAggregatorParams.aggregation_timeout` — intentionally waits
   to allow multi-utterance turns (this is what correctly bundled
   "No." + "Not nothing this month." into one turn in Scenario A)

**Trade-off**: tighter windows = faster response on terse replies, but
more false interruptions when a receptionist pauses mid-sentence.

**Suggested first try**:
- Lower `LLMUserAggregatorParams.aggregation_timeout` to ~0.3s
- Set Deepgram `utterance_end_ms=1000` and `endpointing=300`
- Re-run Scenarios A + B; A's multi-utterance bundling must still
  work, B's "No." should drop under 2s.

**Files**: `server.py` (LLMUserAggregatorParams, DeepgramSTTService).

---

## Agent can't end the call (no programmatic hang-up)

**Observed**: Phase 5 testing, 2026-05-27. Agent says "thanks, goodbye"
but Pipecat's pipeline stays alive until *Twilio* fires
`on_client_disconnected` — which only happens when the human hangs up.
Every test loop currently requires manually hanging up the phone.

**Fix**: give the LLM a `hang_up` function/tool. When invoked, the
handler pushes a Pipecat `EndTaskFrame` (or equivalent) which propagates
to `TwilioFrameSerializer`, which signals Twilio to disconnect.

**Pattern**: Pipecat 1.2.x function-calling — register a tool on the
`AnthropicLLMService`, the handler emits the end frame.

**Files**: `server.py` (LLM tool registration + end-frame handler).

**Adjacent**: this is also the first piece of Phase 4 (CLAUDE.md lists
hang_up under "Tools: speak, send_dtmf, hang_up, report"). Doing it
during Phase 4 instead of separately is fine.

---

## IVR discovery — PII disclosure + data invention (FIXED 2026-05-27)

**Observed**: 2026-05-27, real calls to USPS, GEICO, AT&T.
- USPS IVR asked for ZIP → agent invented `92101` (San Diego)
- GEICO IVR asked for ZIP → agent invented `90210` (Beverly Hills)
- GEICO IVR asked for phone → agent **read the user's actual cell out
  loud** (number redacted) because the SCOPE LIMITS rule said share
  callback "if directly asked". The rule didn't anticipate IVRs
  counting as the asker.

**Fix applied**: server.py SYSTEM_PROMPT_TEMPLATE updated for v0.5's
zero-PII posture:
- PII rule rewritten: never share phone/address/DOB/SSN/member ID/etc.
  to humans OR automated systems, unconditionally. v0.5 tasks don't
  require PII so there is no carve-out.
- NAVIGATING section adds: never fabricate data to satisfy an IVR
  prompt. Press 0 instead.
- Scenarios `pii_request` and `ivr_demands_zip` regression-test this.

**Verify**: eval pass on those scenarios; spot-check via one real call.

---

## IVR discovery — agent says yes to upsells (FIXED 2026-05-27)

**Observed**: 2026-05-27, GEICO call. IVR said "Bundling renters with
auto can mean big savings. Wanna see if it's right for you?" → agent
said "Yes." Took Jay into a bundled-quote funnel he didn't ask for.

**Fix applied**: SCOPE LIMITS rewritten to list soft-sell phrasings
explicitly ("would you like to...", "wanna see if X is right for
you?", "can I sign you up for...", "would you be open to...") and to
spell out bundles/surveys/callbacks/newsletters/promotions as
auto-decline. Scenario `upsell_pushback` regression-tests this.

---

## IVR discovery — agent narrates while pressing keys (FIXED 2026-05-27)

**Observed**: 2026-05-27, USPS call. Agent invoked `send_dtmf("0")`
AND simultaneously said "I'll press 0 to get to an operator" out loud
— the narration was TTS'd to the IVR line.

**Fix applied**: NAVIGATING AUTOMATED MENUS rewritten as positive
instruction: "When pressing a key, emit ONLY the tool call — no
spoken text, no narration." Spot-check via real call still needed —
the eval harness can't verify this since it always records both text
+ tool blocks. **Open question: how to assert "no text alongside
tool_use" in eval.py?**

---

## Transcript doesn't log tool invocations

**Observed**: 2026-05-27, all real calls. `calls/<sid>.json` only
contains text turns. Had to `grep` uvicorn logs to confirm
`send_dtmf` / `end_call` actually fired. Major debugging gap.

**Fix**: `TurnLogObserver` (in `server.py`) should append synthetic
transcript entries for tool invocations, matching the eval-harness
format:
- `send_dtmf("1")` → `{"role": "agent", "text": "[PRESSED: 1]"}`
- `end_call("voicemail")` → `{"role": "agent", "text": "[ENDED CALL: voicemail]"}`

Pipecat hook: observe `FunctionCallInProgressFrame` or
`FunctionCallResultFrame` (check installed package for exact name).

**Files**: `server.py` (TurnLogObserver).

---

## Deepgram TTS deprecation: pass `voice` via Settings

**Observed**: 2026-05-27, every server startup logs:
```
DeprecationWarning: The `voice` parameter is deprecated. Use
`settings=DeepgramTTSService.Settings(voice=...)` instead.
```

**Impact**: cosmetic. Voice still works (aura-2-thalia-en).

**Fix**: change `DeepgramTTSService(api_key=..., voice="...")` to
`DeepgramTTSService(api_key=..., settings=DeepgramTTSService.Settings(voice="..."))`
in `server.py`. ~1 min change.

---

## Scaling notes — 10+ concurrent users (post-v0.5)

**One Twilio number is NOT the concurrency bottleneck.** A number is a
caller ID, not a phone line — a single number can carry many
simultaneous outbound calls. Real Twilio limits: account-level CPS
(default ~1 call-initiation/sec; matters only for batch bursts — space
out `calls.create`) and an account concurrency ceiling that is high and
raisable via support ticket.

What actually breaks at ~10 concurrent users, in order:
1. **Hosting** — laptop + ngrok. Each call is a WS + full Pipecat
   pipeline (VAD/STT/TTS streams). Move to a VPS/Fly/Railway with a
   real domain before inviting strangers. ~1-2 days.
2. **Number reputation, not capacity** — one number dialing many
   dental offices invites carrier spam-labeling ("Spam Likely"), which
   tanks answer rates. Mitigations when it shows up: CNAM registration,
   verified caller ID, then a small number pool with local presence
   (call from the callee's area code).
3. **Same-office collisions** — two users querying the same office →
   double robocalls. Cache-before-call (serve a recent answer instead
   of redialing) fixes this and cuts cost.

**Stripe effort estimate** (for when demand exists; monetization
trigger per v0.5 spec):
- Stripe itself is the easy ~1 day: Checkout payment links for prepaid
  credit packs + one `checkout.session.completed` webhook that credits
  a balance. No subscriptions, no metered billing, no invoicing.
- The prerequisites are the real work: hosted deployment (above), API
  keys + users table (SQLite fine, ~0.5 day), balance check before
  `place_call` + decrement on call end using `duration_s` /
  `est_cost_usd` from record schema v2 (~0.5 day), hosted MCP
  transport for non-technical users (~1 day).
- Total: "Stripe integration" ≈ 1 day; "product strangers can pay for"
  ≈ 1-2 focused weeks on top of the MVP plan.

---

## Roadmap to stranger-ready (kept thin on purpose)

Decision (2026-06-12): do NOT spec this in detail until the friend
test produces data — a deep spec written on zero users decays against
post-MVP code. The MVP plan already future-proofs the load-bearing
seams: metering fields (`duration_s`/`est_cost_usd`), the auth seam
(`DIALAGENT_SECRET` dependency → swap for per-user keys), MCP server
as HTTP client (stdio → hosted transport is a contained swap),
`DIALAGENT_CALLS_DIR` indirection.

Milestone order (each gated, not dated):
1. **Friend alpha** (= MVP plan done) — 5 friends connect to Jay's
   instance via the claude.ai/ChatGPT connector URL (no install, Jay's
   keys, laptop-hosted). Gate to proceed: ≥2 friends return unprompted
   (the v0.5 bar).
2. **Hosted alpha** — VPS/Fly + domain replaces laptop+ngrok; OAuth on
   the connector. ~1-2 days. Gate: a stranger asks to try it, or the
   laptop-must-be-awake constraint visibly costs usage.
3. **Keys + balances** — users table, `dlk_` keys, balance enforcement
   before `place_call`. ~1-2 days.
4. **Stripe credit packs** — checkout links + webhook (see estimate
   above). Gate: free usage costs real money / someone asks to pay.
5. **Number pool / local presence** — only when "Spam Likely" actually
   dents answer rates.

Questions the friend test must answer before specing milestones 2-4
(record answers here):
- Do MCP hosts drive the long-poll loop acceptably, or do calls feel
  lost mid-conversation?
- What do friends actually ask? (task mix vs. the 4 templates)
- Do friends trust the extracted answers, or click-to-dial to verify?
- Does spam labeling appear even at friend volume?
- Does anyone ask for batch? (separate feature axis, v0.6)

Trigger to write the real stranger-ready spec (mvp-plan style, with
gates and tests): friend bar met AND a stranger asks to use it.

Connector polish, post-MVP, only if friction shows up:
- **ChatGPT Custom GPT via Actions** — FastAPI already auto-generates
  `openapi.json`; a Custom GPT with an API-key action is a ChatGPT
  path that needs no developer mode and is shareable by link.
- **Claude Desktop one-click bundle** (`.mcpb` desktop extension)
  instead of JSON-snippet editing — only relevant for the stdio path.
- **Claude connector directory submission** — once hosted + OAuth
  exist; gets discovery instead of pasted URLs.

## STT shutdown warning on call end

**Observed**: Phase 5 testing, 2026-05-27. After every call ends:
```
WARNING | pipecat.utils.asyncio.task_manager:cancel_task:198 -
DeepgramSTTService#0::_connection_handler: timed out waiting for task to cancel
```

**Impact**: cosmetic only. Call ends, transcript is saved, extraction
runs fine.

**Fix**: investigate Pipecat's `cancel_timeout_secs` on `PipelineTask`
(default 20s) or whether Deepgram WS needs an explicit close. Likely a
~5-min change.

**Files**: `server.py` (PipelineTask config).
