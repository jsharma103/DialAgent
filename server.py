import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from xml.sax.saxutils import quoteattr

from dotenv import load_dotenv
from fastapi import FastAPI, Form, HTTPException, WebSocket
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
    extract_result,
    get_calls_dir,
    get_user_profile,
    render_system_prompt,
    require_env,
    save_call_record,
)

load_dotenv()

logger.remove(0)
logger.add(sys.stderr, level="INFO")

STATIC_DIR = Path(__file__).parent / "static"

LIVE_EVENTS: dict[str, asyncio.Queue] = {}


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


async def run_bot(transport: BaseTransport, task: str, call_sid: str) -> None:
    system_instruction = render_system_prompt(task, get_user_profile())
    logger.info(f"TASK: {task}")

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
    logger.info(f"saved call record to {path}")

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
    path = get_calls_dir() / f"{call_sid}.json"
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
