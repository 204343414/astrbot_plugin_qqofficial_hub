"""What actually goes on the wire when a card is sent.

The send path is where a card stops being a dict we control and becomes a QQ
payload we do not. Bugs here are invisible in every unit test that stops at
validation, and they surface in production as a rejected message.
"""
import asyncio
import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace

if "astrbot" not in sys.modules:
    _api = types.ModuleType("astrbot.api")
    _api.logger = SimpleNamespace(**{
        name: (lambda *a, **k: None)
        for name in ("debug", "info", "warning", "error", "exception")
    })
    _root = types.ModuleType("astrbot")
    _root.api = _api
    sys.modules["astrbot"] = _root
    sys.modules["astrbot.api"] = _api

from qqofficial_hub.ephemeral_routes import EphemeralCardMixin  # noqa: E402
from qqofficial_hub.store import PanelStore  # noqa: E402

ORIGIN = "qq:GroupMessage:DCE81C5405474F2682BDB0B112C33A3C"


class _Api:
    def __init__(self):
        self.sent = []

    async def post_group_message(self, **payload):
        self.sent.append(payload)
        return {"id": f"msg{len(self.sent)}"}


class _Client:
    def __init__(self):
        self.api = _Api()


class _Hub(EphemeralCardMixin):
    """The mixin under test, with only the collaborators it really touches."""

    def __init__(self, client):
        self.store = PanelStore(Path(tempfile.mkdtemp()))
        self._client = client
        self._card_providers = {}
        self.recall_superseded_cards = False
        self.show_clicker_name = False
        self.require_known_clicker = False

    def _get_qq_client(self, origin):
        return self._client

    async def _render_dynamic_markdown(self, markdown, origin):
        return markdown


def _send(card):
    client = _Client()
    hub = _Hub(client)

    async def scenario():
        await hub.store.bootstrap()
        await hub.send_ephemeral_card(ORIGIN, card, client=client)

    asyncio.run(scenario())
    return client.api.sent[-1]


def test_a_card_with_no_buttons_omits_the_keyboard_entirely():
    """QQ rejects a keyboard with no rows.

    Picture-only cards are a real case -- a board snapshot, a diagnostic --
    and sending ``rows: []`` turns them into an API error rather than a
    message. Omitting the field is the difference between "no keyboard" and
    "an empty keyboard".
    """
    payload = _send({
        "id": "hub.imageprobe",
        "markdown": "![图 #480px #270px](https://example.com/i/abc.png)",
        "rows": [],
    })
    assert "keyboard" not in payload
    assert payload["msg_type"] == 2
    assert "markdown" in payload


def test_a_card_with_buttons_still_carries_the_keyboard():
    payload = _send({
        "id": "hub.menu",
        "markdown": "选一个",
        "rows": [[{"label": "甲", "insert_text": "/甲"}]],
    })
    rows = payload["keyboard"]["content"]["rows"]
    assert len(rows) == 1
    assert rows[0]["buttons"][0]["action"]["type"] == 2


def test_a_markdown_image_is_passed_through_unmangled():
    """The size hints are part of QQ's syntax, not decoration.

    Dropping or reformatting them changes how the picture renders, so the
    string the caller wrote must reach the wire byte for byte.
    """
    url = "https://favor-prisoner.trycloudflare.com/i/tok3n.png"
    markdown = f"![自检图 #480px #270px]({url})"
    payload = _send({"id": "hub.img", "markdown": markdown, "rows": []})
    assert payload["markdown"]["content"] == markdown
