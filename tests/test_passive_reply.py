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


def test_event_id_reusable_up_to_documented_budget():
    """Docs: a passive reply may be used at most 5 times per event."""
    async def scenario():
        event, sent, proactive = _build()
        for i in range(7):
            await event.send(MessageChain(chain=[Plain(f"m{i}")]))
        assert len(sent) == 5, "must use the full 5-reply budget, no more"
        assert len(proactive) == 2, "overflow falls back instead of vanishing"
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
        assert event._replies_used == 0
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


def test_media_is_uploaded_not_silently_dropped():
    """A chain with an Image must not lose the image."""
    async def scenario():
        from qqofficial_hub import passive_reply as pr

        uploads, sends = [], []

        async def fake_upload(client, ft, src, name, *, group_openid="", user_openid=""):
            uploads.append((ft, src))
            return {"file_info": "FI-1"}

        async def post_group_message(**payload):
            sends.append(payload)

        original = pr._upload_media
        pr._upload_media = fake_upload
        try:
            client = SimpleNamespace(
                api=SimpleNamespace(post_group_message=post_group_message))
            sent = await pr.send_passive(
                client, event_id="E1", text="caption",
                media=[(pr.IMAGE_FILE_TYPE, "/tmp/a.png", None)],
                group_openid="G1",
            )
        finally:
            pr._upload_media = original
        assert uploads == [(pr.IMAGE_FILE_TYPE, "/tmp/a.png")]
        assert sent == 1
        assert sends[0]["msg_type"] == 7
        assert sends[0]["media"] == {"file_info": "FI-1"}
        assert sends[0]["event_id"] == "E1"
        assert sends[0]["content"] == "caption"
    asyncio.run(scenario())


# --- surviving Tencent's own 5xx --------------------------------------------
#
# "系统繁忙，请稍后重试" arrived mid-game and cost the player their whole turn.
# botpy raises ServerError only for HTTP 500/504 (its HttpErrorDict), and the
# official API guide calls this class of error "系统错误，一般重试一次会好".

#: The real botpy source the user runs, so these tests verify its actual error
#: mapping rather than a stub that could quietly encode a wrong assumption.
_BOTPY_CANDIDATES = ("/home/user/botpy",)


def _botpy_errors():
    try:
        import botpy.errors as errors
    except ModuleNotFoundError:
        import os

        for root in _BOTPY_CANDIDATES:
            if os.path.isdir(os.path.join(root, "botpy")):
                sys.path.insert(0, root)
                break
        else:
            pytest.skip("需要 botpy 才能校验其错误映射")
        import botpy.errors as errors
    return errors


def _server_error(message="系统繁忙，请稍后重试"):
    return _botpy_errors().ServerError(msg=message)


def _upload_client(responses):
    """A client whose upload endpoint yields each response/exception in turn."""
    calls = []

    async def request(route, json=None):
        calls.append(json)
        result = responses[len(calls) - 1]
        if isinstance(result, Exception):
            raise result
        return result

    client = SimpleNamespace(api=SimpleNamespace(_http=SimpleNamespace(request=request)))
    return client, calls


def test_upload_retries_a_transient_qq_server_error():
    async def scenario():
        from qqofficial_hub import passive_reply as pr

        client, calls = _upload_client([_server_error(), {"file_info": "FI-1"}])
        pr.MEDIA_UPLOAD_BACKOFF_SECONDS = 0.0
        result = await pr._upload_media(
            client, pr.IMAGE_FILE_TYPE, "base64://QUJD", None, group_openid="G1",
        )
        assert result == {"file_info": "FI-1"}, "重试成功后应正常返回"
        assert len(calls) == 2, "第一次 500 之后应重试"
    asyncio.run(scenario())


def test_upload_gives_up_after_the_documented_number_of_attempts():
    async def scenario():
        ServerError = _botpy_errors().ServerError

        from qqofficial_hub import passive_reply as pr

        client, calls = _upload_client([_server_error()] * pr.MEDIA_UPLOAD_ATTEMPTS)
        pr.MEDIA_UPLOAD_BACKOFF_SECONDS = 0.0
        with pytest.raises(ServerError):
            await pr._upload_media(
                client, pr.IMAGE_FILE_TYPE, "base64://QUJD", None, group_openid="G1",
            )
        assert len(calls) == pr.MEDIA_UPLOAD_ATTEMPTS, "不应无限重试"
    asyncio.run(scenario())


def test_upload_does_not_retry_our_own_bad_request():
    """A 4xx is our bug; retrying it hides the cause and wastes the turn."""
    async def scenario():
        ForbiddenError = _botpy_errors().ForbiddenError

        from qqofficial_hub import passive_reply as pr

        client, calls = _upload_client([ForbiddenError(msg="无权限")])
        pr.MEDIA_UPLOAD_BACKOFF_SECONDS = 0.0
        with pytest.raises(ForbiddenError):
            await pr._upload_media(
                client, pr.IMAGE_FILE_TYPE, "base64://QUJD", None, group_openid="G1",
            )
        assert len(calls) == 1, "4xx 必须立刻抛出，不能重试"
    asyncio.run(scenario())


def test_only_server_error_is_treated_as_retryable():
    """Pin the premise: botpy maps ServerError to 500/504 and nothing else.

    The whole retry policy rests on this. If a future botpy widened
    ServerError to cover a 4xx, retrying would start hiding our own bugs.
    """
    errors = _botpy_errors()
    retryable = {
        code for code, cls in errors.HttpErrorDict.items()
        if cls is errors.ServerError
    }
    assert retryable == {500, 504}


def test_c2c_uses_post_c2c_message():
    async def scenario():
        from qqofficial_hub import passive_reply as pr
        calls = []

        async def post_c2c_message(**payload):
            calls.append(payload)

        client = SimpleNamespace(api=SimpleNamespace(post_c2c_message=post_c2c_message))
        sent = await pr.send_passive(client, event_id="E1", text="hi", user_openid="U1")
        assert sent == 1
        assert calls[0]["openid"] == "U1"
        assert calls[0]["event_id"] == "E1"
    asyncio.run(scenario())


def test_msg_seq_is_monotonic_not_random():
    from qqofficial_hub.passive_reply import next_msg_seq
    values = [next_msg_seq() for _ in range(50)]
    assert len(set(values)) == 50, "msg_seq must never collide"
    assert values == sorted(values)


def test_ack_types_match_official_docs():
    """Only type=11/12 need PUT /interactions; 18/19 are delivered without ACK."""
    from qqofficial_hub import interaction_bridge as ib
    assert ib.ACK_TYPES == {11, 12}
    assert 18 in ib.HANDLED_TYPES and 19 in ib.HANDLED_TYPES
    assert 18 not in ib.ACK_TYPES


def test_interaction_ack_id_and_event_id_are_distinct():
    from qqofficial_hub.passive_reply import interaction_ack_id, passive_event_id
    i = SimpleNamespace(id="ACK-1", event_id="EVT-1")
    assert interaction_ack_id(i) == "ACK-1"
    assert passive_event_id(i) == "EVT-1"
