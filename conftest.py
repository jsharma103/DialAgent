import os
import sys
from pathlib import Path

# Put the project root on sys.path so tests in tests/ can `import server, agent`
# regardless of how pytest is invoked.
sys.path.insert(0, str(Path(__file__).parent))

# The connector mount path is baked from DIALAGENT_SECRET at server-import time,
# so it must be set before `import server`. setdefault preserves a real env value.
os.environ.setdefault("DIALAGENT_SECRET", "test-secret")

import pytest  # noqa: E402

import server  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Every test gets a fresh on-disk calls dir, a known auth secret, and
    clean live registries."""
    monkeypatch.setenv("DIALAGENT_CALLS_DIR", str(tmp_path))
    monkeypatch.setenv("DIALAGENT_SECRET", "test-secret")
    server.LIVE_EVENTS.clear()
    server.ACTIVE_CALLS.clear()
    yield tmp_path
    server.LIVE_EVENTS.clear()
    server.ACTIVE_CALLS.clear()
