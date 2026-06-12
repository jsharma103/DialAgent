import asyncio
import os

import httpx
import pytest

import agent
import server

V2_FIELDS = [
    "call_sid", "to_number", "task", "task_type", "context", "status",
    "created_at", "ended_at", "duration_s", "est_cost_usd", "transcript",
    "result", "error",
]


def asgi_client() -> httpx.AsyncClient:
    # Send the test secret (set by the isolated_state fixture) so protected
    # endpoints authorize. Auth itself is exercised in test_auth.py.
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app),
        base_url="http://test",
        headers={"X-DialAgent-Key": os.environ.get("DIALAGENT_SECRET", "")},
    )


# `isolated_state` (autouse) lives in conftest.py.

# --- /submit: stub record + phone normalization ---

def test_submit_writes_v2_stub(monkeypatch):
    monkeypatch.setattr(server, "place_twilio_call", lambda to, task: "CAtest123")

    async def go():
        async with asgi_client() as ac:
            r = await ac.post(
                "/submit",
                data={
                    "task_type": "plan_acceptance",
                    "phone": "(415) 555-1234",
                    "context": "Delta Dental PPO",
                },
            )
            assert r.status_code == 200
            assert r.json()["call_sid"] == "CAtest123"

            rec = agent.load_call_record("CAtest123")
            assert rec is not None
            assert set(rec) == set(V2_FIELDS)
            assert rec["status"] == "dialing"
            assert rec["to_number"] == "+14155551234"
            assert rec["task_type"] == "plan_acceptance"
            assert rec["context"] == "Delta Dental PPO"
            assert rec["task"] == "Ask if they accept Delta Dental PPO insurance."
            assert rec["created_at"] is not None
            assert rec["ended_at"] is None
            assert rec["duration_s"] is None
            assert rec["est_cost_usd"] is None
            assert rec["transcript"] == []
            assert rec["result"] is None
            # queue created for SSE
            assert "CAtest123" in server.LIVE_EVENTS

    asyncio.run(go())


def test_submit_garbage_phone_is_400(monkeypatch):
    monkeypatch.setattr(server, "place_twilio_call", lambda to, task: "CAx")

    async def go():
        async with asgi_client() as ac:
            r = await ac.post(
                "/submit",
                data={"task_type": "hours", "phone": "not-a-phone", "context": ""},
            )
            assert r.status_code == 400

    asyncio.run(go())


def test_submit_unknown_task_type_is_400(monkeypatch):
    monkeypatch.setattr(server, "place_twilio_call", lambda to, task: "CAx")

    async def go():
        async with asgi_client() as ac:
            r = await ac.post(
                "/submit",
                data={"task_type": "nonsense", "phone": "4155551234", "context": ""},
            )
            assert r.status_code == 400

    asyncio.run(go())


def test_submit_502_when_create_raises(monkeypatch):
    def boom(to, task):
        raise RuntimeError("twilio down")

    monkeypatch.setattr(server, "place_twilio_call", boom)

    async def go():
        async with asgi_client() as ac:
            r = await ac.post(
                "/submit",
                data={"task_type": "hours", "phone": "4155551234", "context": ""},
            )
            assert r.status_code == 502
            # no record written
            assert agent.get_calls_dir().exists() is False or not list(
                agent.get_calls_dir().glob("*.json")
            )

    asyncio.run(go())


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("(415) 555-1234", "+14155551234"),
        ("415-555-1234", "+14155551234"),
        ("415.555.1234", "+14155551234"),
        ("4155551234", "+14155551234"),
        ("14155551234", "+14155551234"),
        ("+1 415 555 1234", "+14155551234"),
        ("+44 20 7123 4567", "+442071234567"),
    ],
)
def test_normalize_phone_ok(raw, expected):
    assert agent.normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["abc", "123", "555-12", "", "+", "++14155551234"])
def test_normalize_phone_bad(raw):
    with pytest.raises(ValueError):
        agent.normalize_phone(raw)


# --- /call-status: kill the no-answer black hole ---

@pytest.mark.parametrize(
    "twilio_status,expected",
    [
        ("no-answer", "no_answer"),
        ("busy", "busy"),
        ("failed", "failed"),
        ("canceled", "failed"),
    ],
)
def test_callback_terminalizes_dialing(monkeypatch, twilio_status, expected):
    monkeypatch.setattr(server, "place_twilio_call", lambda to, task: "CAd")

    async def go():
        async with asgi_client() as ac:
            await ac.post(
                "/submit",
                data={"task_type": "plan_acceptance", "phone": "4155551234", "context": "Delta"},
            )
            assert "CAd" in server.LIVE_EVENTS

            r = await ac.post(
                "/call-status",
                data={"CallSid": "CAd", "CallStatus": twilio_status, "CallDuration": "0"},
            )
            assert r.status_code == 204

            rec = agent.load_call_record("CAd")
            assert rec["status"] == expected
            assert rec["ended_at"] is not None
            assert rec["duration_s"] is not None
            assert rec["est_cost_usd"] is not None

            # live queue got the terminal sentinel
            assert server.LIVE_EVENTS["CAd"].get_nowait() is None

            # /result returns the terminal record
            rr = await ac.get("/result/CAd")
            assert rr.status_code == 200
            assert rr.json()["status"] == expected

    asyncio.run(go())


def test_callback_completed_while_dialing_is_error(monkeypatch):
    monkeypatch.setattr(server, "place_twilio_call", lambda to, task: "CAd")

    async def go():
        async with asgi_client() as ac:
            await ac.post(
                "/submit",
                data={"task_type": "hours", "phone": "4155551234", "context": ""},
            )
            r = await ac.post(
                "/call-status",
                data={"CallSid": "CAd", "CallStatus": "completed", "CallDuration": "5"},
            )
            assert r.status_code == 204
            rec = agent.load_call_record("CAd")
            assert rec["status"] == "error"
            assert "never connected" in rec["error"]

    asyncio.run(go())


def test_callback_on_terminal_is_noop():
    async def go():
        rec = agent.new_call_record(
            "CAterm", to_number="+14155551234", task="t", task_type="hours", context=None
        )
        rec["status"] = "completed"
        rec["duration_s"] = 42
        rec["est_cost_usd"] = agent.est_cost(42)
        rec["ended_at"] = agent.now_iso()
        agent.save_call_record("CAterm", rec)

        async with asgi_client() as ac:
            r = await ac.post(
                "/call-status",
                data={"CallSid": "CAterm", "CallStatus": "completed", "CallDuration": "99"},
            )
            assert r.status_code == 204
            after = agent.load_call_record("CAterm")
            assert after["status"] == "completed"
            assert after["duration_s"] == 42  # untouched

    asyncio.run(go())


def test_callback_unknown_sid_is_noop():
    async def go():
        async with asgi_client() as ac:
            r = await ac.post(
                "/call-status",
                data={"CallSid": "CAnope", "CallStatus": "completed", "CallDuration": "10"},
            )
            assert r.status_code == 204

    asyncio.run(go())


def test_callback_backfills_in_progress_without_touching_status():
    async def go():
        rec = agent.new_call_record(
            "CAip", to_number="+14155551234", task="t", task_type="hours", context=None
        )
        rec["status"] = "in_progress"
        agent.save_call_record("CAip", rec)

        async with asgi_client() as ac:
            r = await ac.post(
                "/call-status",
                data={"CallSid": "CAip", "CallStatus": "completed", "CallDuration": "77"},
            )
            assert r.status_code == 204
            after = agent.load_call_record("CAip")
            assert after["status"] == "in_progress"  # WS path owns status
            assert after["duration_s"] == 77
            assert after["est_cost_usd"] == agent.est_cost(77)

    asyncio.run(go())
