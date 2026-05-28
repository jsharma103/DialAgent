import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any
from xml.sax.saxutils import quoteattr

from anthropic import AsyncAnthropic
from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, WebSocket
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from loguru import logger
from twilio.rest import Client as TwilioClient
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.dtmf.types import KeypadEntry
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    EndTaskFrame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    OutputDTMFFrame,
    TranscriptionFrame,
)
from pipecat.observers.base_observer import BaseObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.stt_service import STTService
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

load_dotenv(override=True)

logger.remove(0)
logger.add(sys.stderr, level="INFO")

PROFILE_PATH = Path(__file__).parent / "user_profile.json"
CALLS_DIR = Path(__file__).parent / "calls"
STATIC_DIR = Path(__file__).parent / "static"

LIVE_EVENTS: dict[str, asyncio.Queue] = {}

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


USER_PROFILE = load_user_profile()


class TurnLogObserver(BaseObserver):
    def __init__(self, call_sid: str = "") -> None:
        super().__init__()
        self._buf: list[str] = []
        self.turns: list[dict[str, str]] = []
        self.call_sid = call_sid

    async def _emit(self, turn: dict[str, str]) -> None:
        queue = LIVE_EVENTS.get(self.call_sid)
        if queue is not None:
            await queue.put(turn)

    async def on_push_frame(self, data) -> None:
        frame = data.frame
        if isinstance(frame, TranscriptionFrame) and isinstance(data.source, STTService):
            turn = {"role": "user", "text": frame.text}
            self.turns.append(turn)
            await self._emit(turn)
            logger.info(f"USER: {frame.text}")
        elif isinstance(frame, LLMTextFrame):
            self._buf.append(frame.text)
        elif isinstance(frame, LLMFullResponseEndFrame) and self._buf:
            text = "".join(self._buf).strip()
            turn = {"role": "agent", "text": text}
            self.turns.append(turn)
            await self._emit(turn)
            logger.info(f"AGENT: {text}")
            self._buf = []


async def extract_result(task: str, turns: list[dict[str, str]]) -> dict[str, Any]:
    client = AsyncAnthropic(api_key=require_env("ANTHROPIC_API_KEY"))
    transcript_str = "\n".join(f"{t['role'].upper()}: {t['text']}" for t in turns)
    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
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
    CALLS_DIR.mkdir(exist_ok=True)
    path = CALLS_DIR / f"{call_sid}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, indent=2))
    tmp.replace(path)
    return path


async def run_bot(transport: BaseTransport, task: str, call_sid: str) -> None:
    system_instruction = render_system_prompt(task, USER_PROFILE)
    logger.info(f"TASK: {task}")

    llm = AnthropicLLMService(
        api_key=require_env("ANTHROPIC_API_KEY"),
        settings=AnthropicLLMService.Settings(
            model="claude-haiku-4-5-20251001",
            system_instruction=system_instruction,
            enable_prompt_caching=True,
        ),
    )

    async def end_call_handler(params: FunctionCallParams) -> None:
        reason = params.arguments.get("reason", "")
        logger.info(f"end_call invoked: {reason!r}")
        await params.result_callback({"status": "ending call"})
        await params.llm.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)

    async def send_dtmf_handler(params: FunctionCallParams) -> None:
        digits = params.arguments.get("digits", "")
        keys: list[KeypadEntry] = []
        for d in digits:
            try:
                keys.append(KeypadEntry(d))
            except ValueError:
                logger.warning(f"invalid DTMF digit: {d!r}")
        if not keys:
            await params.result_callback({"status": "error", "message": "no valid digits"})
            return
        logger.info(f"send_dtmf invoked: {digits!r}")
        await params.result_callback({"status": "ok", "digits": digits})
        await params.llm.push_frame(OutputDTMFFrame(buttons=keys))

    llm.register_function("end_call", end_call_handler)
    llm.register_function("send_dtmf", send_dtmf_handler)

    stt = DeepgramSTTService(api_key=require_env("DEEPGRAM_API_KEY"))
    tts = DeepgramTTSService(api_key=require_env("DEEPGRAM_API_KEY"), voice="aura-2-thalia-en")

    context = LLMContext(tools=ToolsSchema(standard_tools=[END_CALL_SCHEMA, SEND_DTMF_SCHEMA]))
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_aggregator,
            llm,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    observer = TurnLogObserver(call_sid)
    pipeline_task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
        ),
        idle_timeout_secs=1200,
        observers=[observer],
    )

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("call ended; cancelling pipeline")
        await pipeline_task.cancel()

    await PipelineRunner(handle_sigint=False).run(pipeline_task)

    record: dict[str, Any] = {
        "call_sid": call_sid,
        "task": task,
        "transcript": observer.turns,
        "result": None,
        "error": None,
    }
    try:
        record["result"] = await extract_result(task, observer.turns)
        logger.info(f"RESULT: {json.dumps(record['result'], indent=2)}")
    except Exception as e:
        record["error"] = f"{type(e).__name__}: {e}"
        logger.exception("extraction failed")
    path = save_call_record(call_sid, record)
    logger.info(f"saved call record to {path.relative_to(Path(__file__).parent)}")

    queue = LIVE_EVENTS.get(call_sid)
    if queue is not None:
        await queue.put(None)


async def handle_call(websocket: WebSocket) -> None:
    _transport_type, call_data = await parse_telephony_websocket(websocket)
    task = call_data.get("body", {}).get("task") or "(no task specified)"

    serializer = TwilioFrameSerializer(
        stream_sid=call_data["stream_id"],
        call_sid=call_data["call_id"],
        account_sid=require_env("TWILIO_ACCOUNT_SID"),
        auth_token=require_env("TWILIO_AUTH_TOKEN"),
    )

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=serializer,
        ),
    )

    await run_bot(transport, task, call_data["call_id"])


def place_twilio_call(to_number: str, task: str) -> str:
    ngrok_url = require_env("NGROK_URL").rstrip("/")
    ws_url = ngrok_url.replace("https://", "wss://").replace("http://", "ws://") + "/ws"
    twiml = (
        f"<Response><Connect><Stream url=\"{ws_url}\">"
        f"<Parameter name=\"task\" value={quoteattr(task)} />"
        f"</Stream></Connect></Response>"
    )
    client = TwilioClient(require_env("TWILIO_ACCOUNT_SID"), require_env("TWILIO_AUTH_TOKEN"))
    call = client.calls.create(
        to=to_number,
        from_=require_env("TWILIO_PHONE_NUMBER"),
        twiml=twiml,
    )
    return call.sid


app = FastAPI()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/submit")
async def submit(
    task_type: str = Form(...),
    phone: str = Form(...),
    context: str = Form(""),
) -> dict[str, str]:
    template = TASK_TEMPLATES.get(task_type)
    if template is None:
        raise HTTPException(400, f"unknown task_type: {task_type!r}")
    task = template.format(context=context.strip())
    logger.info(f"submit: task_type={task_type!r} phone={phone!r} task={task!r}")
    call_sid = place_twilio_call(phone, task)
    LIVE_EVENTS[call_sid] = asyncio.Queue()
    logger.info(f"placed call sid={call_sid}")
    return {"call_sid": call_sid, "task": task}


@app.get("/events/{call_sid}")
async def events(call_sid: str) -> StreamingResponse:
    async def gen():
        queue = LIVE_EVENTS.get(call_sid)
        if queue is None:
            yield 'data: {"error":"unknown call"}\n\n'
            return
        try:
            while True:
                turn = await queue.get()
                if turn is None:
                    yield 'data: {"done":true}\n\n'
                    break
                yield f"data: {json.dumps(turn)}\n\n"
        finally:
            LIVE_EVENTS.pop(call_sid, None)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/result/{call_sid}")
async def result(call_sid: str) -> JSONResponse:
    path = CALLS_DIR / f"{call_sid}.json"
    if not path.exists():
        raise HTTPException(404, "result not yet available")
    return JSONResponse(json.loads(path.read_text()))


@app.websocket("/ws")
async def media_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    logger.info("websocket connected")
    try:
        await handle_call(websocket)
    except Exception:
        logger.exception("websocket handler crashed")
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
