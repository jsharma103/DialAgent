# Product brief

The durable description of what DialAgent is and isn't. Doesn't change
between phases.

---

## What it does

```
User: "Call Bay Dental at (415) 555-1234 and ask if they take Delta Dental
       PPO and have Thursday afternoon availability for a cleaning."

DialAgent:
  - Places the outbound call via Twilio
  - Navigates the IVR (DTMF + voice-directed)
  - Waits on hold without complaint
  - Asks the questions in natural language
  - Records the conversation, extracts structured answers
  - Reports back: { accepts_insurance: yes, availability: "Thu 3pm", notes: ... }
```

This is **Mode 1 — autonomous scout**. Mode 2 (bridging the user into the
call for high-stakes turns like booking/confirmation) is later, not v0.

---

## Why this exists

- Everyone hates making phone calls. Hold times, IVR mazes, voicemail tag.
- LLMs are now good enough to navigate calls naturally and tools are mature
  (Pipecat handles real-time pipelines, Twilio handles telephony, Deepgram
  handles low-latency ASR/TTS).
- Cost is ~$0.04–0.07/min — making calls is functionally free at v0 scale.
- The user (Jay) personally hates phone calls and would use this Monday.

The original framing was an accessibility-wedge play (AI relay for callers
with speech impairments) tied to the YC Voice Agents Hackathon. We've
dropped both — Jay isn't in the hackathon, and the accessibility wedge
needed a real user with a speech impairment we don't have. We're building
the general product for Jay first.

This may eventually be a YC application. Don't let that warp the v0 — ship
something that works for one user before thinking about a company.

---

## Original v0 spec (still applies)

The smallest thing that's actually useful:

| | |
|---|---|
| **Input** | Plain-English task + a phone number |
| **Output** | Transcript + structured answers (JSON) |
| **Scope** | One call per task. No parallel. No conference bridging. |
| **UI** | Probably a CLI or one-page web form. Web form is nicer for demo. |
| **Persistence** | Save every call: task, audio, transcript, agent decisions, result. |

Success criterion for v0: Jay can type "Call X and ask Y" into a form, hit
go, and 2 minutes later see a structured answer he can trust without
listening to the recording himself.

For the v0.5 narrowing (dental shopping concierge), see
`v0.5-shopping-concierge.md`.

---

## Architectural notes

### It's not an RL problem
LLMs already know how to navigate phone calls. The work is prompt
engineering + good tools + clean context — same pattern as any agent.

```
LLM brain
  ├─ System prompt (who you are, what to do, user info, escalation rules)
  ├─ Tools: speak(text), send_dtmf(digit), hang_up(), report(result)
  └─ Input: streaming ASR transcript of what it hears
```

### IVR is the easy part
Predictable structured menus. LLM reads "press 1 for X, press 2 for Y,"
picks the right one, calls `send_dtmf`. Twilio's
`client.calls(sid).update(twiml='<Play digits="1"/>')` does the rest.

### What's in the system prompt matters more than the model
Spend disproportionate time on the system prompt: how to be polite, when to
escalate, what info to share vs. never share (SSN, credit card), how to
handle "is this a robot?" gracefully.

### Log everything
Every turn produces a record:
```json
{
  "turn": 3, "ts": "00:12.4",
  "heard": "For appointments press 1...",
  "thought": "I need scheduling, pressing 1",
  "action": "send_dtmf(1)"
}
```
This is the data for both debugging and the future eval loop.

---

## Non-goals (deliberately)

Don't build these in v0 even if they seem obvious:

- Mobile app — the user UI is a web page on Jay's laptop
- Multi-user SaaS — single user (Jay), local-only
- Authentication / OAuth — n/a
- Real-time speech mediation (Mode 2) — Mode 1 must be solid first
- Voice cloning — generic Deepgram voice is fine
- HIPAA / compliance — n/a until there's a real second user
- "Production-ready" anything — this is v0

The pull toward building too much is the biggest risk. Resist.

---

## What "done with v0" looks like

A 60-second demo video of Jay typing a real task into a web form, watching
the live transcript as DialAgent calls a real business, and seeing the
result land. The agent succeeds at one realistic task end-to-end. That's
the milestone. Everything else is post-v0.
