# Stack and setup

Reference material. Read when onboarding a new session or debugging env.

---

## Stack

- **Pipecat** — real-time voice pipeline framework
  (https://github.com/pipecat-ai/pipecat). Used `twilio-chatbot` example
  as the starting point.
- **Twilio** — outbound calling + Media Streams (WebSocket audio).
  Account + phone number (~$1.15) + ngrok for local webhook tunneling.
- **Deepgram** — ASR (Nova-3) + TTS (Aura-2 Thalia). Cheap, fast.
- **Anthropic** — LLM for the agent brain (Haiku 4.5 for the agent,
  Sonnet 4.6 as the eval judge).
- **FastAPI** — minimal web server for the trigger endpoint + TwiML + WS.
- **JSON files** — call history under `calls/`. No DB yet.

---

## Skip for v0 (deliberately)

- NVIDIA NIM, open-weights self-hosting (premature optimization)
- Multi-tenant architecture, AWS deployment (it's running on Jay's laptop)
- Personalized ASR fine-tuning
- HIPAA / encrypted vault
- Cekura integration (eval comes after the agent works at all)

---

## Tactical setup checklist

Before writing any code, make sure these exist:

- [ ] Python 3.11 venv at `.venv/` (use `/Users/jay/.local/bin/python3.11`)
- [ ] Twilio account with one phone number purchased
- [ ] Deepgram account (free credit)
- [ ] Anthropic API key
- [ ] ngrok account for local webhook tunneling (reserved domain helps)
- [ ] `.env` file with: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`,
      `TWILIO_PHONE_NUMBER`, `JAY_CELL`, `DEEPGRAM_API_KEY`,
      `ANTHROPIC_API_KEY`, `NGROK_URL`

Dependencies are pinned in `requirements.txt`.

---

## Context links

Source materials behind this project. Read if anything in the briefs is
unclear:

- `/Users/jay/projects/adhoc/SYNTHESIS.md` — full strategic synthesis,
  winner analysis, voice agent hackathon framing, receptionist-vs-caller
  pivot history.
- `/Users/jay/projects/adhoc/chat-transcript.jsonl` — original session
  transcript that led to this project.
- `/Users/jay/projects/adhoc/b03088be-a5f2-47f0-8436-74a778d2de30.jsonl`
  — second session on the accessibility wedge, FCC 24-84, Mode 1/Mode 2
  split, IVR navigation patterns, "harness IS the product" insight.
- `https://github.com/jsharma103/adhoc` — private repo with the above.
