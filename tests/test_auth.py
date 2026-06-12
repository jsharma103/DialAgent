import asyncio
import os

import httpx
import pytest

import server


def noauth_client() -> httpx.AsyncClient:
    """Client that sends NO key — for exercising the auth boundary."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=server.app), base_url="http://test"
    )


def test_protected_endpoints_401_without_key():
    async def go():
        async with noauth_client() as ac:
            for path in ["/calls", "/status/CAx", "/result/CAx", "/events/CAx"]:
                r = await ac.get(path)
                assert r.status_code == 401, path
            r = await ac.post("/submit", json={"phone": "4155551234", "task": "hi"})
            assert r.status_code == 401

    asyncio.run(go())


def test_protected_endpoint_401_with_wrong_key():
    async def go():
        async with noauth_client() as ac:
            r = await ac.get("/calls", headers={"X-DialAgent-Key": "nope"})
            assert r.status_code == 401
            r2 = await ac.get("/calls", params={"key": "nope"})
            assert r2.status_code == 401

    asyncio.run(go())


def test_protected_endpoint_200_with_header():
    async def go():
        secret = os.environ["DIALAGENT_SECRET"]
        async with noauth_client() as ac:
            r = await ac.get("/calls", headers={"X-DialAgent-Key": secret})
            assert r.status_code == 200
            assert r.json() == []

    asyncio.run(go())


def test_protected_endpoint_200_with_query_param():
    async def go():
        secret = os.environ["DIALAGENT_SECRET"]
        async with noauth_client() as ac:
            r = await ac.get("/calls", params={"key": secret})
            assert r.status_code == 200

    asyncio.run(go())


def test_submit_200_with_key(monkeypatch):
    monkeypatch.setattr(server, "place_twilio_call", lambda to, task: "CAauth")

    async def go():
        secret = os.environ["DIALAGENT_SECRET"]
        async with noauth_client() as ac:
            r = await ac.post(
                "/submit",
                json={"phone": "4155551234", "task": "hi"},
                headers={"X-DialAgent-Key": secret},
            )
            assert r.status_code == 200
            assert r.json()["call_sid"] == "CAauth"

    asyncio.run(go())


def test_call_status_is_keyless():
    async def go():
        async with noauth_client() as ac:
            # No key sent: a protected endpoint would 401; /call-status returns
            # 204 (unknown sid), proving it's open to Twilio.
            r = await ac.post(
                "/call-status", data={"CallSid": "CAx", "CallStatus": "completed"}
            )
            assert r.status_code == 204

    asyncio.run(go())


def test_index_page_is_open():
    async def go():
        async with noauth_client() as ac:
            r = await ac.get("/")
            assert r.status_code == 200

    asyncio.run(go())


def test_startup_fails_fast_without_secret(monkeypatch):
    monkeypatch.delenv("DIALAGENT_SECRET", raising=False)

    async def go():
        with pytest.raises(RuntimeError):
            async with server.lifespan(server.app):
                pass

    asyncio.run(go())
