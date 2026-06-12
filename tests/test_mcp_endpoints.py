import asyncio

import httpx

import agent
import server


def asgi_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app), base_url="http://test"
    )


# --- JSON /submit (both shapes) ---

def test_submit_json_free_text(monkeypatch):
    monkeypatch.setattr(server, "place_twilio_call", lambda to, task: "CAfree")

    async def go():
        async with asgi_client() as ac:
            r = await ac.post(
                "/submit", json={"phone": "4155551234", "task": "Ask about Saturday hours"}
            )
            assert r.status_code == 200
            assert r.json()["call_sid"] == "CAfree"
            rec = agent.load_call_record("CAfree")
            assert rec["status"] == "dialing"
            assert rec["task"] == "Ask about Saturday hours"
            assert rec["task_type"] is None
            assert rec["context"] is None
            assert rec["to_number"] == "+14155551234"

    asyncio.run(go())


def test_submit_json_task_type(monkeypatch):
    monkeypatch.setattr(server, "place_twilio_call", lambda to, task: "CAtyped")

    async def go():
        async with asgi_client() as ac:
            r = await ac.post(
                "/submit",
                json={"phone": "(415) 555-1234", "task_type": "pricing", "context": "a cleaning"},
            )
            assert r.status_code == 200
            rec = agent.load_call_record("CAtyped")
            assert rec["task_type"] == "pricing"
            assert rec["context"] == "a cleaning"
            assert rec["task"] == "Ask what the cash price is for a cleaning."

    asyncio.run(go())


def test_submit_json_missing_task_and_type_is_400(monkeypatch):
    monkeypatch.setattr(server, "place_twilio_call", lambda to, task: "CAx")

    async def go():
        async with asgi_client() as ac:
            r = await ac.post("/submit", json={"phone": "4155551234"})
            assert r.status_code == 400

    asyncio.run(go())


# --- /status long-poll ---

def test_status_terminal_returns_immediately():
    async def go():
        rec = agent.new_call_record(
            "CAdone", to_number="+14155551234", task="t", task_type="hours", context=None
        )
        rec["status"] = "completed"
        rec["result"] = {"summary": "Open 9-5."}
        agent.save_call_record("CAdone", rec)
        async with asgi_client() as ac:
            r = await ac.get("/status/CAdone", params={"wait": 30})
            assert r.status_code == 200
            body = r.json()
            assert body["status"] == "completed"
            assert body["result"]["summary"] == "Open 9-5."

    asyncio.run(go())


def test_status_unknown_sid_is_404():
    async def go():
        async with asgi_client() as ac:
            r = await ac.get("/status/CAnope", params={"wait": 0})
            assert r.status_code == 404

    asyncio.run(go())


def test_status_longpoll_returns_terminal_with_partial_transcript():
    async def go():
        sid = "CAlp"
        obs = server.TurnLogObserver(sid)
        obs.turns.append({"role": "user", "text": "Bay Dental, hi"})
        server.ACTIVE_CALLS[sid] = obs
        rec = agent.new_call_record(
            sid, to_number="+14155551234", task="t", task_type="hours", context=None
        )
        rec["status"] = "in_progress"
        agent.save_call_record(sid, rec)

        async with asgi_client() as ac:
            # partial-transcript path: in_progress + wait=0 -> live observer turns
            r0 = await ac.get(f"/status/{sid}", params={"wait": 0})
            assert r0.status_code == 200
            assert r0.json()["status"] == "in_progress"
            assert {"role": "user", "text": "Bay Dental, hi"} in r0.json()["transcript"]

            async def finisher():
                await asyncio.sleep(1.2)
                obs.turns.append({"role": "agent", "text": "thanks, bye"})
                done = agent.load_call_record(sid)
                done["status"] = "completed"
                done["transcript"] = list(obs.turns)
                agent.save_call_record(sid, done)
                server.ACTIVE_CALLS.pop(sid, None)  # mimic run_bot's finally

            task = asyncio.create_task(finisher())
            r = await ac.get(f"/status/{sid}", params={"wait": 8})
            await task
            assert r.status_code == 200
            assert r.json()["status"] == "completed"  # returned before the 8s timeout
            assert {"role": "agent", "text": "thanks, bye"} in r.json()["transcript"]

    asyncio.run(go())


# --- /calls ---

def test_calls_newest_first_and_respects_limit():
    async def go():
        for i, ts in enumerate(
            [
                "2026-06-12T10:00:00+00:00",
                "2026-06-12T11:00:00+00:00",
                "2026-06-12T12:00:00+00:00",
            ]
        ):
            rec = agent.new_call_record(
                f"CA{i}", to_number=f"+1415555000{i}", task=f"task {i}", task_type="hours", context=None
            )
            rec["created_at"] = ts
            rec["status"] = "completed"
            rec["result"] = {"summary": f"summary {i}"}
            agent.save_call_record(f"CA{i}", rec)

        async with asgi_client() as ac:
            r = await ac.get("/calls", params={"limit": 2})
            assert r.status_code == 200
            body = r.json()
            assert len(body) == 2
            assert body[0]["call_sid"] == "CA2"  # newest
            assert body[1]["call_sid"] == "CA1"
            assert body[0]["summary"] == "summary 2"
            assert set(body[0]) == {
                "call_sid", "to_number", "task", "status", "created_at", "summary"
            }

    asyncio.run(go())


def test_calls_empty_is_empty_list():
    async def go():
        async with asgi_client() as ac:
            r = await ac.get("/calls")
            assert r.status_code == 200
            assert r.json() == []

    asyncio.run(go())


# --- MCP layer wiring (thin: just confirm the 3 tools register) ---

def test_mcp_server_registers_three_tools():
    import mcp_server

    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {"place_call", "get_call_status", "list_recent_calls"}
