"""INTERACTION_CREATE must be answered as a *passive* message.

QQ docs (server-inter/message/send-receive/send.html) define a passive message
as one carrying msg_id/event_id, and list "INTERACTION_CREATE" among the events
whose id may be used as event_id. Sending without it degrades the reply to a
proactive push, which requires the group owner to enable proactive messages and
burns the 4-per-month proactive quota.
"""
import asyncio
import sys
import types
from types import SimpleNamespace

import pytest


def _install_astrbot_stubs():
    if "astrbot.api.event" in sys.modules:
        return

    class MessageChain:
        def __init__(self, chain=None):
            self.chain = chain or []
            self.type = "normal"

    class AstrMessageEvent:
        def __init__(self, command, message, meta, session_id):
            self.message_str = command
            self.message_obj = message
            self.session = SimpleNamespace(session_id=session_id)
            self._extras = {}

        def set_extra(self, key, value):
            self._extras[key] = value

        async def send(self, message):
            return None

        async def send_streaming(self, generator, use_fallback=False):
            return None

    class Plain:
        def __init__(self, text=""):
            self.text = text

    class At:
        def __init__(self, qq=""):
            self.qq = qq

    class AstrBotMessage:
        pass

    class MessageMember:
        def __init__(self, user_id, nickname=""):
            self.user_id = user_id
            self.nickname = nickname

    class MessageType:
        GROUP_MESSAGE = "GroupMessage"

    api = types.ModuleType("astrbot.api")
    api.logger = SimpleNamespace(
        warning=lambda *a, **k: None,
        info=lambda *a, **k: None,
        exception=lambda *a, **k: None,
    )
    event_mod = types.ModuleType("astrbot.api.event")
    event_mod.AstrMessageEvent = AstrMessageEvent
    event_mod.MessageChain = MessageChain
    comp_mod = types.ModuleType("astrbot.api.message_components")
    comp_mod.At = At
    comp_mod.Plain = Plain
    plat_mod = types.ModuleType("astrbot.api.platform")
    plat_mod.AstrBotMessage = AstrBotMessage
    plat_mod.MessageMember = MessageMember
    plat_mod.MessageType = MessageType

    sys.modules.setdefault("astrbot", types.ModuleType("astrbot"))
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.message_components"] = comp_mod
    sys.modules["astrbot.api.platform"] = plat_mod


_install_astrbot_stubs()

from astrbot.api.event import MessageChain  # noqa: E402
from astrbot.api.message_components import Plain  # noqa: E402
from qqofficial_hub.command_dispatch import HubSyntheticCommandEvent  # noqa: E402


def _build(event_id="EVT-1", group="G1", interaction_id="INTERACTION-9"):
    sent = []
    proactive = []

    async def post_group_message(**payload):
        sent.append(payload)

    async def send_by_session(session, message):
        proactive.append((session, message))

    client = SimpleNamespace(api=SimpleNamespace(post_group_message=post_group_message))
    adapter = SimpleNamespace(
        meta=lambda: SimpleNamespace(id="qq_official"),
        send_by_session=send_by_session,
    )
    # Mirror botpy: envelope id -> .event_id, payload d["id"] -> .id
    interaction = SimpleNamespace(id=interaction_id, event_id=event_id,
                                  group_openid=group, group_member_openid="M1")
    event = HubSyntheticCommandEvent("/新闻", adapter, client, interaction)
    return event, sent, proactive


def test_reply_uses_event_id_and_avoids_proactive_push():
    async def scenario():
        event, sent, proactive = _build()
        await event.send(MessageChain(chain=[Plain("头条来了")]))
        assert len(sent) == 1, "should send exactly one passive message"
        assert sent[0]["event_id"] == "EVT-1", "must use .event_id, not .id"
        assert sent[0]["event_id"] != "INTERACTION-9"
        assert "msg_id" not in sent[0]
        assert sent[0]["content"] == "头条来了"
        assert sent[0]["group_openid"] == "G1"
        assert proactive == [], "must not fall back to proactive send"
    asyncio.run(scenario())


def test_event_id_is_spent_only_once_then_falls_back():
    async def scenario():
        event, sent, proactive = _build()
        await event.send(MessageChain(chain=[Plain("first")]))
        await event.send(MessageChain(chain=[Plain("second")]))
        assert len(sent) == 1, "event_id must not be reused"
        assert len(proactive) == 1, "second reply falls back to proactive path"
    asyncio.run(scenario())


def test_falls_back_to_proactive_when_qq_rejects_event_id():
    async def scenario():
        event, sent, proactive = _build()

        async def boom(**payload):
            raise RuntimeError("event_id expired")

        event.bot.api.post_group_message = boom
        await event.send(MessageChain(chain=[Plain("hi")]))
        assert sent == []
        assert len(proactive) == 1, "must not silently drop the reply"
    asyncio.run(scenario())


def test_missing_event_id_uses_proactive_path():
    async def scenario():
        event, sent, proactive = _build(event_id="")
        await event.send(MessageChain(chain=[Plain("hi")]))
        assert sent == []
        assert len(proactive) == 1
    asyncio.run(scenario())


def test_empty_text_does_not_consume_event_id():
    async def scenario():
        event, sent, proactive = _build()
        await event.send(MessageChain(chain=[]))
        assert sent == []
        assert len(proactive) == 1
        assert event._event_id_used is False
    asyncio.run(scenario())


def test_passive_event_id_reads_event_id_not_interaction_id():
    """botpy: Interaction(api, payload["id"], payload["d"]).

    ``.id`` is the interaction_id for PUT /interactions/{id} (ACK);
    ``.event_id`` is the platform event id valid for passive replies.
    Using ``.id`` yields QQ error "请求参数event_id无效".
    """
    from qqofficial_hub.command_dispatch import passive_event_id
    both = SimpleNamespace(id="INTERACTION-9", event_id="EVT-1")
    assert passive_event_id(both) == "EVT-1"
    assert passive_event_id(SimpleNamespace(id="INTERACTION-9")) == ""
    assert passive_event_id(SimpleNamespace(id="X", event_id=None)) == ""
    assert passive_event_id(SimpleNamespace(id="X", event_id="  E  ")) == "E"
