"""Push-state inference must be decisive when possible, silent when unsure."""
import asyncio
import tempfile
from pathlib import Path

from qqofficial_hub import push_probe as pp
from qqofficial_hub.store import PanelStore


def test_push_related_errors_are_classified_revoked():
    for message in [
        "请@群主开启 Bot 的主动消息功能",
        "机器人未开启主动推送",
        "该群未开启主动发言",
        "no permission to send proactive message",
        "code 11244",
    ]:
        assert pp.classify_send_error(message) == pp.REVOKED, message


def test_unrelated_errors_never_claim_revoked():
    """An audit/rate-limit failure must not brand a healthy group as disabled."""
    for message in [
        "请求参数event_id无效",
        "push message is waiting for audit now",
        "消息发送频率超限",
        "rate limit exceeded",
        "invalid parameter: msg_id",
        "",
    ]:
        assert pp.classify_send_error(message) == pp.UNKNOWN, message


def test_authorize_outranks_send():
    assert pp.should_replace(pp.REVOKED, "send", pp.GRANTED, "authorize")
    assert not pp.should_replace(pp.GRANTED, "authorize", pp.REVOKED, "send")


def test_same_source_allows_flip():
    assert pp.should_replace(pp.GRANTED, "authorize", pp.REVOKED, "authorize")


def test_unknown_never_overwrites_a_known_state():
    assert not pp.should_replace(pp.GRANTED, "send", pp.UNKNOWN, "authorize")


def test_unknown_is_always_replaceable():
    assert pp.should_replace(pp.UNKNOWN, "", pp.REVOKED, "send")


def test_group_openid_of():
    assert pp.group_openid_of("qq_official:GroupMessage:ABC123") == "ABC123"
    assert pp.group_openid_of("qq_official:FriendMessage:U1") == ""
    assert pp.group_openid_of("") == ""


def test_store_honours_precedence_end_to_end():
    async def scenario():
        store = PanelStore(Path(tempfile.mkdtemp()))
        await store.bootstrap()
        origin = "qq_official:GroupMessage:G1"
        await store.set_push_state(origin, pp.REVOKED, "send")
        assert await store.get_push_state(origin) == pp.REVOKED
        await store.set_push_state(origin, pp.GRANTED, "authorize")
        assert await store.get_push_state(origin) == pp.GRANTED
        # a weaker send observation must not undo an authorize event
        await store.set_push_state(origin, pp.REVOKED, "send")
        assert await store.get_push_state(origin) == pp.GRANTED
    asyncio.run(scenario())


def test_adapter_source_is_rejected_on_write():
    """`_allow_group_proactive_send` is a hard-coded True; never trust it."""
    async def scenario():
        store = PanelStore(Path(tempfile.mkdtemp()))
        await store.bootstrap()
        origin = "qq_official:GroupMessage:G1"
        await store.set_push_state(origin, pp.GRANTED, "adapter")
        assert await store.get_push_state(origin) == pp.UNKNOWN
    asyncio.run(scenario())


def test_poisoned_adapter_state_on_disk_reads_as_unknown():
    """Existing installs already wrote a bogus green lamp; scrub it on read."""
    async def scenario():
        d = Path(tempfile.mkdtemp())
        store = PanelStore(d)
        await store.bootstrap()
        origin = "qq_official:GroupMessage:G1"
        # simulate what the retracted heuristic persisted
        store._data.setdefault("push_states", {})[origin] = {
            "state": "granted", "source": "adapter", "updated_at": 1,
        }
        store._write_atomic(store._data)
        again = PanelStore(d)
        await again.bootstrap()
        assert await again.get_push_state(origin) == pp.UNKNOWN
        # and a genuine signal still lands
        await again.set_push_state(origin, pp.REVOKED, "send")
        assert await again.get_push_state(origin) == pp.REVOKED
    asyncio.run(scenario())


def test_skip_detection_reports_revoked_not_silence():
    """The adapter skips silently; that skip must still light the lamp red."""
    from qqofficial_hub import push_probe as probe
    msg = "skip send_by_session: 群未开启主动消息"
    assert probe.classify_send_error(msg) == probe.REVOKED
