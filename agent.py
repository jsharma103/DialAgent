"""Agent brain + shared call logic. No import-time side effects.

This module is imported by both `server.py` (the FastAPI/Pipecat entrypoint)
and `eval.py` (the harness). It must not read env vars, load dotenv, configure
logging, or load the user profile at import time — those are deferred to first
use so importing `agent` is always safe (tests, fresh checkouts, no `.env`).
"""

import json
import os
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic
from pipecat.adapters.schemas.function_schema import FunctionSchema

CALL_MODEL = "claude-haiku-4-5-20251001"
EXTRACT_MODEL = "claude-haiku-4-5-20251001"
EST_COST_PER_MIN = 0.05

PROFILE_PATH = Path(__file__).parent / "user_profile.json"
DEFAULT_CALLS_DIR = Path(__file__).parent / "calls"


def get_calls_dir() -> Path:
    """Where call records live. Env-overridable so tests can redirect it.

    Read at call time, never bound at import — `DIALAGENT_CALLS_DIR` is set
    per-test.
    """
    return Path(os.environ.get("DIALAGENT_CALLS_DIR", str(DEFAULT_CALLS_DIR)))


TASK_TEMPLATES: dict[str, str] = {
    "plan_acceptance": "Ask if they accept {context} insurance.",
    "pricing": "Ask what the cash price is for {context}.",
    "hours": "Ask what their hours are on weekdays.",
    "procedure_availability": "Ask if they offer {context}, and if so, whether they do it in-house or refer out.",
}

END_CALL_SCHEMA = FunctionSchema(
    name="end_call",
    description=(
        "Hang up the phone call. Invoke this AFTER you have said goodbye to the other person and the call is complete — "
        "for example after you've gotten the information you needed, the person can't help, or it's time to escalate. "
        "Do not invoke until you've actually said goodbye in your reply."
    ),
    properties={
        "reason": {
            "type": "string",
            "description": "Brief reason for hanging up (e.g. 'got the answers', 'no availability — escalating', 'voicemail').",
        },
    },
    required=["reason"],
)


SEND_DTMF_SCHEMA = FunctionSchema(
    name="send_dtmf",
    description=(
        "Press one or more keys on the phone keypad to navigate an IVR menu. "
        "Use this when you hear a menu prompt like 'press 1 for appointments, press 2 for billing'. "
        "Match the menu option to your task. If no option fits, press 0 (operator) or whatever option routes to a human."
    ),
    properties={
        "digits": {
            "type": "string",
            "description": "Digits to press. Single digit ('1') or sequence ('12' for 1 then 2). Valid: 0-9, *, #.",
        },
    },
    required=["digits"],
)


REPORT_TOOL: dict[str, Any] = {
    "name": "report_result",
    "description": "Report the structured outcome of the phone call.",
    "input_schema": {
        "type": "object",
        "properties": {
            "task_completed": {
                "type": "boolean",
                "description": "Whether the task's information was successfully obtained.",
            },
            "summary": {
                "type": "string",
                "description": "1-2 sentence summary of what was learned on the call.",
            },
            "answers": {
                "type": "object",
                "description": "Key/value pairs of the questions asked and the answers received. Use snake_case keys inferred from the task. Values may be boolean, string, number, or null if unanswered.",
            },
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": "Confidence in the extracted answers given the transcript.",
            },
            "notes": {
                "type": "string",
                "description": "Any caveats, ambiguities, or things the user should know.",
            },
        },
        "required": ["task_completed", "summary", "answers", "confidence", "notes"],
    },
}

SYSTEM_PROMPT_TEMPLATE = """\
You are placing an outbound phone call on behalf of {user_name}.

YOUR TASK:
{task}

WHAT YOU KNOW ABOUT {user_name} (share only if relevant to the task):
{profile_block}

HOW TO INTRODUCE YOURSELF:
Wait for the person to greet you first (e.g. "Hello?" or the business name). Then open with a brief, conversational line such as:
- "Hi, I'm calling on behalf of {user_name} to ask about..."
- "Hey there — quick question on behalf of {user_name}..."

CRITICAL: Do NOT lead with "I'm an AI assistant" or mention being AI, a bot, or automated in your intro. Sound like a friendly person making a quick call.

NAVIGATING AUTOMATED MENUS (IVR):
- If you hear a phone menu like "press 1 for appointments, press 2 for billing", use the send_dtmf tool to press the correct key.
- Pick the option that best matches your task. If none fit, press 0 (operator) or whatever option routes to a human.
- When pressing a key, emit ONLY the tool call — no spoken text, no narration, no "I'll press 1 to get to..." explanation. The IVR doesn't need commentary; just the digit, silently.
- NEVER invent data to satisfy an IVR prompt. If an IVR asks for a ZIP code, member ID, account number, phone number, or anything else you weren't explicitly given in YOUR TASK or WHAT YOU KNOW above, do NOT fabricate one. Press 0 instead to escape to a human operator.

IF YOU REACH VOICEMAIL:
- A voicemail greeting sounds like "You've reached [name]. Please leave a message after the tone..." or "Sorry we missed your call...".
- Do NOT leave a message. Invoke end_call with reason "voicemail".

HOLD / SILENCE:
- If you hear hold music or extended silence after speaking, wait patiently. Do not fill silence with chatter.
- A real receptionist will speak again when they're ready.

SCOPE LIMITS (do not exceed your task):
- Stick to exactly what your task says to do. If the task says to ASK a question, only ask — don't accept bookings, reservations, or appointments on {user_name}'s behalf.
- Decline ALL offers beyond your task. Watch especially for soft-sell phrasings: "would you like to...", "wanna see if X is right for you?", "can I sign you up for...", "would you be open to a follow-up call?", "want me to go ahead and book that?", "interested in our newsletter?", "want to hear about our special?". All of these are offers. Decline politely: "Not today, thanks — {user_name} will follow up if he wants to proceed." Then steer back to your task.
- Do not agree to surveys, callbacks, bundles, promotions, newsletters, or appointments — even if framed as a casual yes/no question.

OTHER RULES:
- Sound warm and natural — like a friendly person making a quick phone call, not a customer service script. Use contractions ("I'm", "you're", "they're"). It's fine to start replies with "Yeah," "Oh," "Got it," etc. when it feels natural.
- Keep replies short, usually 1-2 sentences. Don't be robotic about it — sometimes a one-word "Yeah!" or "Got it!" is the right reply.
- If the person directly asks whether you're a robot, AI, or automated: confirm honestly that you are. Do not lie when directly asked.
- NEVER share personal information about {user_name}: phone number, address, date of birth, Social Security Number, member ID, account numbers, credit card, passwords, email. This applies to BOTH humans AND automated systems. Even if directly asked. Even if the IVR refuses to proceed without it — press 0 for an operator instead. If pressed by a human, say: "I don't have that to share — {user_name} would need to call back directly."
- If the person needs to transfer you, escalate, or says they can't help: say "Thank you, I'll have {user_name} follow up directly," and end the call.
- Once you have the information your task needs, thank them and say goodbye. Do not prolong the call.
"""


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing env var: {name}")
    return value


def load_user_profile() -> dict:
    if not PROFILE_PATH.exists():
        raise RuntimeError(f"missing user profile at {PROFILE_PATH}")
    with PROFILE_PATH.open() as f:
        return json.load(f)


_profile: dict | None = None


def get_user_profile() -> dict:
    """Lazy, module-cached user profile. Loaded on first use, not at import."""
    global _profile
    if _profile is None:
        _profile = load_user_profile()
    return _profile


def format_profile_block(profile: dict) -> str:
    lines: list[str] = []
    for key, value in profile.items():
        if key in {"notes", "name"}:
            continue
        label = key.replace("_", " ").capitalize()
        if isinstance(value, dict):
            sub = ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in value.items())
            lines.append(f"- {label}: {sub}")
        else:
            lines.append(f"- {label}: {value}")
    return "\n".join(lines)


def render_system_prompt(task: str, profile: dict) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        user_name=profile["name"],
        task=task,
        profile_block=format_profile_block(profile),
    )


async def extract_result(task: str, turns: list[dict[str, str]]) -> dict[str, Any]:
    client = AsyncAnthropic(api_key=require_env("ANTHROPIC_API_KEY"))
    transcript_str = "\n".join(f"{t['role'].upper()}: {t['text']}" for t in turns)
    response = await client.messages.create(
        model=EXTRACT_MODEL,
        max_tokens=1024,
        system="You extract structured outcomes from phone call transcripts. Use the report_result tool.",
        messages=[
            {
                "role": "user",
                "content": (
                    f"TASK: {task}\n\n"
                    f"TRANSCRIPT:\n{transcript_str}\n\n"
                    "Report the structured outcome using report_result."
                ),
            }
        ],
        tools=[REPORT_TOOL],
        tool_choice={"type": "tool", "name": "report_result"},
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "report_result":
            return block.input
    raise RuntimeError("extractor did not call report_result")


def save_call_record(call_sid: str, record: dict[str, Any]) -> Path:
    calls_dir = get_calls_dir()
    calls_dir.mkdir(parents=True, exist_ok=True)
    path = calls_dir / f"{call_sid}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2))
    tmp.replace(path)
    return path
