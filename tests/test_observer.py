import asyncio

import server


def test_log_tool_appends_turn_and_emits_to_queue():
    async def go():
        sid = "CAobs"
        queue = asyncio.Queue()
        server.LIVE_EVENTS[sid] = queue
        try:
            obs = server.TurnLogObserver(sid)
            await obs.log_tool("[PRESSED: 1]")
            assert obs.turns == [{"role": "agent", "text": "[PRESSED: 1]"}]
            assert queue.get_nowait() == {"role": "agent", "text": "[PRESSED: 1]"}

            await obs.log_tool("[ENDED CALL: voicemail]")
            assert obs.turns[-1] == {"role": "agent", "text": "[ENDED CALL: voicemail]"}
            assert queue.get_nowait() == {"role": "agent", "text": "[ENDED CALL: voicemail]"}
        finally:
            server.LIVE_EVENTS.pop(sid, None)

    asyncio.run(go())


def test_log_tool_without_registered_queue_does_not_raise():
    async def go():
        obs = server.TurnLogObserver("CAnoqueue")
        await obs.log_tool("[PRESSED: 0]")  # no queue registered
        assert obs.turns[-1]["text"] == "[PRESSED: 0]"

    asyncio.run(go())
