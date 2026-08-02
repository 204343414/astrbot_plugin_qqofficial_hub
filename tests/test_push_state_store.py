"""Push state persistence: only proactive sends may infer state."""
import asyncio
import tempfile
from pathlib import Path

from qqofficial_hub.store import PanelStore


def test_push_state_defaults_to_unknown_and_round_trips():
    async def scenario():
        store = PanelStore(Path(tempfile.mkdtemp()))
        await store.bootstrap()
        origin = "qq_official:GroupMessage:G1"
        assert await store.get_push_state(origin) == "unknown"
        await store.set_push_state(origin, "revoked", "send")
        assert await store.get_push_state(origin) == "revoked"
        await store.set_push_state(origin, "granted", "authorize")
        assert await store.get_push_state(origin) == "granted"
        # unrelated group stays unknown
        assert await store.get_push_state("qq_official:GroupMessage:G2") == "unknown"
    asyncio.run(scenario())


def test_push_state_rejects_non_group_origin():
    async def scenario():
        store = PanelStore(Path(tempfile.mkdtemp()))
        await store.bootstrap()
        await store.set_push_state("qq_official:FriendMessage:U1", "granted", "send")
        assert await store.get_push_state("qq_official:FriendMessage:U1") == "unknown"
    asyncio.run(scenario())


def test_push_state_survives_reload():
    async def scenario():
        d = Path(tempfile.mkdtemp())
        store = PanelStore(d)
        await store.bootstrap()
        await store.set_push_state("qq_official:GroupMessage:G1", "revoked", "send")
        again = PanelStore(d)
        await again.bootstrap()
        assert await again.get_push_state("qq_official:GroupMessage:G1") == "revoked"
    asyncio.run(scenario())
