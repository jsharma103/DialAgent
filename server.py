import asyncio
import hmac
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from xml.sax.saxutils import quoteattr

from dotenv import load_dotenv
from fastapi import (
    Depends,
    FastAPI,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
)
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from loguru import logger
from twilio.rest import Client as TwilioClient
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

from agent import (
    CALL_MODEL,
    END_CALL_SCHEMA,
    SEND_DTMF_SCHEMA,
    TASK_TEMPLATES,
    TERMINAL_STATUSES,
    est_cost,
    extract_result,
    finalize_timing,
    get_calls_dir,
    get_user_profile,
    load_call_record,
    new_call_record,
    normalize_phone,
    render_system_prompt,
    require_env,
    save_call_record,
)

load_dotenv()

logger.remove(0)
logger.add(sys.stderr, level="INFO")

STATIC_DIR = Path(__file__).parent / "static"

# SSE event queues, keyed by call_sid. Drained by /events.
LIVE_EVENTS: dict[str, asyncio.Queue] = {}
# Live observers for in-progress calls, keyed by call_sid. Source of truth for
# partial transcripts read by the /status endpoint (Phase 4).
ACTIVE_CALLS: dict[str, "TurnLogObserver"] = {}


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

    async def log_tool(self, text: str) -> None:
        """Record a tool invocation as an agent turn (e.g. [PRESSED: 1],
        [ENDED CALL: voicemail]). Matches the eval-transcript format."""
        turn = {"role": "agent", "text": text}
        self.turns.append(turn)
        await self._emit(turn)
        logger.info(f"AGENT: {text}")

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


async def run_bot(transport: BaseTransport, task: str, call_sid: str) -> None:
    system_instruction = render_system_prompt(task, get_user_profile())
    logger.info(f"TASK: {task}")

    observer = TurnLogObserver(call_sid)

    llm = AnthropicLLMService(
        api_key=require_env("ANTHROPIC_API_KEY"),
        settings=AnthropicLLMService.Settings(
            model=CALL_MODEL,
            system_instruction=system_instruction,
            enable_prompt_caching=True,
        ),
    )

    async def end_call_handler(params: FunctionCallParams) -> None:
        reason = params.arguments.get("reason", "")
        logger.info(f"end_call invoked: {reason!r}")
        await observer.log_tool(f"[ENDED CALL: {reason}]")
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
        await observer.log_tool(f"[PRESSED: {digits}]")
        await params.result_callback({"status": "ok", "digits": digits})
        await params.llm.push_frame(OutputDTMFFrame(buttons=keys))

    llm.register_function("end_call", end_call_handler)
    llm.register_function("send_dtmf", send_dtmf_handler)

    stt = DeepgramSTTService(api_key=require_env("DEEPGRAM_API_KEY"))
    tts = DeepgramTTSService(
        api_key=require_env("DEEPGRAM_API_KEY"),
        settings=DeepgramTTSService.Settings(voice="aura-2-thalia-en"),
    )

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

    pipeline_task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
        ),
        idle_timeout_secs=600,
        observers=[observer],
    )

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("call ended; cancelling pipeline")
        await pipeline_task.cancel()

    # WS is connected: register the live observer and mark in_progress.
    ACTIVE_CALLS[call_sid] = observer
    record = load_call_record(call_sid)
    if record is not None and record["status"] == "dialing":
        record["status"] = "in_progress"
        save_call_record(call_sid, record)

    try:
        await PipelineRunner(handle_sigint=False).run(pipeline_task)

        record = load_call_record(call_sid) or new_call_record(call_sid, to_number=None, task=task)
        record["status"] = "extracting"
        record["transcript"] = observer.turns
        save_call_record(call_sid, record)

        try:
            record["result"] = await extract_result(task, observer.turns)
            logger.info(f"RESULT: {json.dumps(record['result'], indent=2)}")
        except Exception as e:
            record["error"] = f"{type(e).__name__}: {e}"
            logger.exception("extraction failed")

        record["status"] = "completed"
        finalize_timing(record)
        path = save_call_record(call_sid, record)
        logger.info(f"saved call record to {path}")
    except Exception as e:
        logger.exception("run_bot failed")
        record = load_call_record(call_sid) or new_call_record(call_sid, to_number=None, task=task)
        record["status"] = "error"
        record["error"] = f"{type(e).__name__}: {e}"
        record["transcript"] = observer.turns
        finalize_timing(record)
        save_call_record(call_sid, record)
    finally:
        queue = LIVE_EVENTS.get(call_sid)
        if queue is not None:
            await queue.put(None)
        ACTIVE_CALLS.pop(call_sid, None)


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
        status_callback=f"{ngrok_url}/call-status",
        status_callback_event=["completed"],
        status_callback_method="POST",
    )
    return call.sid


@asynccontextmanager
async def lifespan(app: FastAPI):
    require_env("DIALAGENT_SECRET")  # fail fast: the form/MCP secret must be set
    yield


def verify_key(
    x_dialagent_key: str | None = Header(default=None, alias="X-DialAgent-Key"),
    key: str | None = Query(default=None),
) -> None:
    """Auth dependency for protected endpoints. Accepts the secret via the
    X-DialAgent-Key header (MCP) or ?key= query param (web form / EventSource,
    which can't set headers). Constant-time compare; 401 otherwise."""
    expected = require_env("DIALAGENT_SECRET")
    provided = x_dialagent_key or key or ""
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(401, "invalid or missing key")


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/submit", dependencies=[Depends(verify_key)])
async def submit(request: Request) -> dict[str, str]:
    """Place a call. Accepts form data (web form: task_type + context) or
    JSON (MCP: free-text `task`, or `task_type` + `context`)."""
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("application/json"):
        body = await request.json()
    else:
        body = dict(await request.form())

    phone = (body.get("phone") or "").strip()
    if not phone:
        raise HTTPException(400, "missing phone")
    try:
        to_number = normalize_phone(phone)
    except ValueError as e:
        raise HTTPException(400, str(e))

    task_type = body.get("task_type") or None
    free_task = (body.get("task") or "").strip()
    if task_type:
        template = TASK_TEMPLATES.get(task_type)
        if template is None:
            raise HTTPException(400, f"unknown task_type: {task_type!r}")
        ctx = (body.get("context") or "").strip()
        task = template.format(context=ctx)
        ctx = ctx or None
    elif free_task:
        task = free_task
        task_type = None
        ctx = None
    else:
        raise HTTPException(400, "must provide either 'task' or 'task_type'")

    logger.info(f"submit: phone={to_number!r} task_type={task_type!r} task={task!r}")
    try:
        call_sid = place_twilio_call(to_number, task)
    except Exception as e:
        logger.exception("calls.create failed")
        raise HTTPException(502, f"failed to place call: {e}")

    record = new_call_record(
        call_sid, to_number=to_number, task=task, task_type=task_type, context=ctx
    )
    save_call_record(call_sid, record)
    LIVE_EVENTS[call_sid] = asyncio.Queue()
    logger.info(f"placed call sid={call_sid}")
    return {"call_sid": call_sid, "task": task}


@app.post("/call-status")
async def call_status_callback(
    call_sid: str = Form(..., alias="CallSid"),
    twilio_status: str = Form(..., alias="CallStatus"),
    call_duration: str | None = Form(None, alias="CallDuration"),
) -> Response:
    record = load_call_record(call_sid)
    if record is None or record["status"] in TERMINAL_STATUSES:
        return Response(status_code=204)  # unknown sid or already terminal: idempotent no-op

    duration = None
    if call_duration:
        try:
            duration = int(call_duration)
        except ValueError:
            duration = None

    if record["status"] == "dialing":
        # The media stream never connected — this callback owns the outcome.
        if twilio_status == "completed":
            record["status"] = "error"
            record["error"] = "call completed but media stream never connected"
        elif twilio_status == "no-answer":
            record["status"] = "no_answer"
        elif twilio_status == "busy":
            record["status"] = "busy"
        else:  # failed, canceled, or anything unexpected
            record["status"] = "failed"
        finalize_timing(record, duration)
        save_call_record(call_sid, record)
        queue = LIVE_EVENTS.get(call_sid)
        if queue is not None:
            await queue.put(None)
        logger.info(f"call-status {call_sid}: {twilio_status} -> {record['status']}")
    else:
        # in_progress / extracting: the WS path owns status; only backfill timing.
        if record.get("duration_s") is None and duration is not None:
            record["duration_s"] = duration
            record["est_cost_usd"] = est_cost(duration)
            save_call_record(call_sid, record)

    return Response(status_code=204)


@app.get("/events/{call_sid}", dependencies=[Depends(verify_key)])
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


@app.get("/result/{call_sid}", dependencies=[Depends(verify_key)])
async def result(call_sid: str) -> JSONResponse:
    path = get_calls_dir() / f"{call_sid}.json"
    if not path.exists():
        raise HTTPException(404, "result not yet available")
    return JSONResponse(json.loads(path.read_text()))


def _status_snapshot(record: dict[str, Any], call_sid: str) -> dict[str, Any]:
    """Full record, but with live observer turns when the call is in-flight."""
    snap = dict(record)
    observer = ACTIVE_CALLS.get(call_sid)
    if observer is not None:
        snap["transcript"] = list(observer.turns)
    return snap


@app.get("/status/{call_sid}", dependencies=[Depends(verify_key)])
async def status(call_sid: str, wait: int = 0) -> JSONResponse:
    """Long-poll: block until the call reaches a terminal status or `wait`
    seconds (clamped 0–120) elapse, then return the full current snapshot."""
    wait = max(0, min(120, wait))
    elapsed = 0
    while True:
        record = load_call_record(call_sid)
        if record is None:
            raise HTTPException(404, "unknown call")
        if record["status"] in TERMINAL_STATUSES or elapsed >= wait:
            return JSONResponse(_status_snapshot(record, call_sid))
        await asyncio.sleep(1)
        elapsed += 1


@app.get("/calls", dependencies=[Depends(verify_key)])
async def list_calls(limit: int = 10) -> JSONResponse:
    calls_dir = get_calls_dir()
    records: list[dict[str, Any]] = []
    if calls_dir.exists():
        for path in calls_dir.glob("*.json"):
            try:
                records.append(json.loads(path.read_text()))
            except (OSError, json.JSONDecodeError):
                continue
    records.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    out = []
    for r in records[: max(0, limit)]:
        res = r.get("result")
        summary = res.get("summary") if isinstance(res, dict) else None
        out.append(
            {
                "call_sid": r.get("call_sid"),
                "to_number": r.get("to_number"),
                "task": r.get("task"),
                "status": r.get("status"),
                "created_at": r.get("created_at"),
                "summary": summary,
            }
        )
    return JSONResponse(out)


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
