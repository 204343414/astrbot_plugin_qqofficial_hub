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


def test_authorize_outranks_send_which_outranks_adapter():
    assert pp.should_replace(pp.REVOKED, "adapter", pp.GRANTED, "send")
    assert pp.should_replace(pp.REVOKED, "send", pp.GRANTED, "authorize")
    # weaker source cannot override a stronger one
    assert not pp.should_replace(pp.GRANTED, "authorize", pp.REVOKED, "adapter")
    assert not pp.should_replace(pp.GRANTED, "send", pp.REVOKED, "adapter")


def test_same_source_allows_flip():
    assert pp.should_replace(pp.GRANTED, "authorize", pp.REVOKED, "authorize")


def test_unknown_never_overwrites_a_known_state():
    assert not pp.should_replace(pp.GRANTED, "send", pp.UNKNOWN, "authorize")


def test_unknown_is_always_replaceable():
    assert pp.should_replace(pp.UNKNOWN, "", pp.REVOKED, "adapter")


def test_group_openid_of():
    assert pp.group_openid_of("qq_official:GroupMessage:ABC123") == "ABC123"
    assert pp.group_openid_of("qq_official:FriendMessage:U1") == ""
    assert pp.group_openid_of("") == ""


def test_store_honours_precedence_end_to_end():
    async def scenario():
        store = PanelStore(Path(tempfile.mkdtemp()))
        await store.bootstrap()
        origin = "qq_official:GroupMessage:G1"
        await store.set_push_state(origin, pp.REVOKED, "adapter")
        assert await store.get_push_state(origin) == pp.REVOKED
        # a real authorize event wins
        await store.set_push_state(origin, pp.GRANTED, "authorize")
        assert await store.get_push_state(origin) == pp.GRANTED
        # a weak adapter reading must not undo it
        await store.set_push_state(origin, pp.REVOKED, "adapter")
        assert await store.get_push_state(origin) == pp.GRANTED
    asyncio.run(scenario())
