# v0 build order

The original 7-phase order. Each phase should be runnable end-to-end
before moving to the next.

---

1. **Hello-world** — DONE. Call Jay's cell from a Python script; agent says
   "hello, this is DialAgent" and hangs up. Proved Twilio + TwiML pipeline.

2. **Pipecat pipeline** — DONE. Same but the audio flows through Pipecat
   (Twilio WS → STT → LLM → TTS → Twilio WS) instead of static TwiML.

3. **Task-driven prompt** — DONE. System prompt takes a task ("ask X if Y")
   and user context (Jay's DOB, insurance). Agent pursues the task on a
   call.

4. **IVR navigation** — DONE. Agent sends DTMF tones via `send_dtmf` tool,
   recognizes voicemail and invokes `end_call`, sits silently through hold.
   Verified against USPS, GEICO, AT&T IVRs.

5. **Structured extraction** — DONE. After the call ends, an LLM pass over
   the transcript produces a JSON result against the task's expected
   schema.

6. **Test harness** — DONE. YAML scenarios, three-model split
   (agent=Haiku, receptionist=Haiku, judge=Sonnet). The harness IS the
   product — prompt evolves based on what fails.

7. **Distribution layer** — IN PROGRESS as **v0.5 — dental shopping
   concierge, MCP-first**. Web form built at `localhost:8000` (now a
   secondary mobile/demo surface). MCP server exposing DialAgent as a
   tool for Claude / Gemini / OpenAI hosts is the primary distribution
   shape. See `v0.5-shopping-concierge.md`.

---

Stop here for v0. Mode 2, parallel calls, voice cloning, caller-ID
spoofing, multi-vertical — all later.
