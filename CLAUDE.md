# DialAgent

A voice agent that makes phone calls on the user's behalf. The user (Jay)
is the first user.

**Current focus**: v0.5 — dental shopping concierge, **MCP-first**
distribution (DialAgent exposed as a tool for Claude / Gemini / OpenAI
hosts; the web form at `localhost:8000` is a secondary mobile/demo
surface). See `docs/v0.5-shopping-concierge.md` for the active spec.

This file is the entry point for a fresh Claude Code session. The
durable briefs live in `docs/` and are imported below. Read them before
suggesting anything.

@docs/product.md
@docs/v0.5-shopping-concierge.md
@docs/v0-build-order.md
@docs/stack-setup.md

---

## Working agreement

- Default to writing no comments unless the *why* is non-obvious.
- No abstractions for single-use code.
- No error handling for impossible scenarios.
- No "production-grade" anything in v0.
- Match Jay's existing style: terse, direct, structured.
- Verify before claiming "done" — run the actual call, don't just compile.
- When in doubt about scope, re-read **Non-goals** in `docs/product.md`
  and **Out of scope** in `docs/v0.5-shopping-concierge.md`.
