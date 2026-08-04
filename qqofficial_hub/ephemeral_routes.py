"""Ephemeral-card routing: click handling, sending and provider registry.

Split out of ``main.py`` so the plugin class stays about lifecycle, commands
and configuration. See ``docs/EPHEMERAL_CARDS.md`` for the contract.
"""
from __future__ import annotations

import base64
import time
from typing import Any

from astrbot.api import logger

from .action_registry import ActionContext, EphemeralContext
from . import ephemeral
from .passive_reply import (
    next_msg_seq,
    passive_event_id,
    real_msg_id,
    recall_message,
    within_recall_window,
)


class EphemeralCardMixin:
    """One-shot cards for flows and games."""

    async def _clicker_allowed(self, origin: str, member_openid: str) -> bool:
        """Refuse users the bot has never heard speak.

        INTERACTION_CREATE carries no nickname, so an unknown OpenID cannot be
        attributed to anyone. Letting strangers drive cards is what makes a
        group griefable with a single tap; requiring one prior message raises
        the cost without inconveniencing real participants.
        """
        if not getattr(self, "require_known_clicker", True):
            return True
        try:
            if await self.identities.is_known(origin, member_openid):
                return True
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("[QQHub] Identity check failed, allowing: %s", exc)
            return True
        logger.info(
            "[QQHub] Rejected unknown clicker %s in %s",
            member_openid[-6:], origin.split(":", 2)[-1][-8:],
        )
        return False

    async def _clicker_header(self, origin: str, member_openid: str) -> str:
        """A plain-text 'who did this' line. Never a real @ mention.

        Field testing showed QQ renders both documented and legacy At tags
        literally on this path, so a plain label is the honest option.
        """
        if not getattr(self, "show_clicker_name", True) or not member_openid:
            return ""
        try:
            label = await self.identities.label_for(origin, member_openid)
        except Exception:  # pragma: no cover - defensive
            return ""
        return f"👤 {label}" if label else ""

    async def _handle_ephemeral_click(
        self, client: Any, interaction: Any, nonce: str, button_id: str
    ) -> int:
        group_openid = str(getattr(interaction, "group_openid", "") or "")
        if not group_openid:
            return ephemeral.CODE_FAILED
        origin = f"{client.platform.meta().id}:GroupMessage:{group_openid}"
        member = str(getattr(interaction, "group_member_openid", "") or "")
        if not await self._clicker_allowed(origin, member):
            return ephemeral.CODE_FORBIDDEN
        try:
            button, record = await self.store.claim_ephemeral_click(
                origin, nonce, button_id, member
            )
        except ephemeral.EphemeralError as exc:
            logger.info("[QQHub] Ephemeral click refused: %s", exc)
            return exc.code
        except Exception:
            logger.exception("[QQHub] Ephemeral click failed")
            return ephemeral.CODE_FAILED

        session_id = str(record.get("session_id") or "")
        event_id = passive_event_id(interaction)

        # Static flow: a declared next_card needs no plugin code at all.
        next_card_id = str(button.get("next_card") or "")
        if next_card_id:
            provider = self._card_providers.get(next_card_id)
            if provider is None:
                logger.warning("[QQHub] Unknown next_card: %s", next_card_id)
                return ephemeral.CODE_FAILED
            try:
                card = await provider(EphemeralContext(
                    client=client, interaction=interaction, origin=origin,
                    group_openid=group_openid, member_openid=member,
                    session_id=session_id, params=button.get("params") or {},
                ))
                if card is not None:
                    await self.send_ephemeral_card(
                        origin, card, client=client,
                        session_id=session_id, event_id=event_id,
                        initiator_openid=member,
                    )
            except Exception:
                logger.exception("[QQHub] next_card provider failed: %s", next_card_id)
                return ephemeral.CODE_FAILED
            return ephemeral.CODE_OK

        action_id = str(button.get("action_id") or "")
        if not action_id:
            return ephemeral.CODE_FAILED
        self._sync_command_actions()
        context = ActionContext(
            client=client, interaction=interaction, origin=origin,
            group_openid=group_openid, member_openid=member,
        )
        params = dict(button.get("params") or {})
        params.setdefault("_session_id", session_id)
        return await self.actions.execute(action_id, context, params)

    @staticmethod
    def _prepend_header(markdown: str, header: str) -> str:
        return f"{header}\n{markdown}" if header else markdown

    async def send_image_message(
        self,
        origin: str,
        image: bytes,
        text: str = "",
        client: Any = None,
        event_id: str | None = None,
        msg_id: str | None = None,
    ) -> str:
        """Send one image and return the QQ message id it was given.

        Games whose board is a picture need that id so they can require players
        to *quote the board* when replying with a move. Without it there is no
        way to tell a real move from someone typing "H8" in conversation.

        Returns "" when QQ does not report an id; callers should degrade to
        accepting un-quoted moves rather than blocking play.
        """
        from .passive_reply import IMAGE_FILE_TYPE

        return await self.send_media_message(
            origin, image, IMAGE_FILE_TYPE, text=text, client=client,
            event_id=event_id, msg_id=msg_id,
        )

    async def send_media_message(
        self,
        origin: str,
        data: bytes,
        file_type: int,
        text: str = "",
        client: Any = None,
        event_id: str | None = None,
        msg_id: str | None = None,
    ) -> str:
        """Upload any rich media and send it, returning the QQ message id.

        ``file_type`` follows QQ's own numbering: 1 image, 2 video, 3 voice,
        4 file. Voice accepts silk/wav/mp3/flac, so a synthesised chord can go
        out on exactly the same path as a game board.
        """
        from .passive_reply import _upload_media, real_msg_id

        client = client or self._get_qq_client(origin)
        group_openid = origin.split(":", 2)[-1]
        uploaded = await _upload_media(
            client, file_type, "base64://" + base64.b64encode(data).decode(),
            None, group_openid=group_openid,
        )
        if uploaded is None:
            raise RuntimeError("富媒体上传失败")
        payload: dict[str, Any] = {
            "group_openid": group_openid,
            "msg_type": 7,
            "media": {"file_info": uploaded["file_info"]},
            "msg_seq": next_msg_seq(),
        }
        if text:
            payload["content"] = text
        msg_id = real_msg_id(msg_id)
        if msg_id:
            payload["msg_id"] = msg_id
        elif event_id:
            payload["event_id"] = event_id
        result = await client.api.post_group_message(**payload)
        for attr in ("id", "msg_id", "message_id"):
            value = getattr(result, attr, None) or (
                result.get(attr) if isinstance(result, dict) else None
            )
            if value:
                return str(value)
        return ""

    async def recall_message(self, origin: str, message_id: str,
                             client: Any = None, sent_at: float | None = None) -> bool:
        """Recall a message this bot sent. Public API for game/flow plugins.

        Returns False instead of raising: a failed recall must never break the
        thing it was tidying up after. QQ only allows this within two minutes
        of sending, so pass ``sent_at`` to skip a request that cannot succeed.
        """
        if sent_at is not None and not within_recall_window(sent_at):
            return False
        client = client or self._get_qq_client(origin)
        if client is None:
            return False
        if "GroupMessage" in origin:
            return await recall_message(
                client, message_id, group_openid=origin.split(":", 2)[-1])
        return await recall_message(
            client, message_id, user_openid=origin.split(":", 2)[-1])

    async def send_ephemeral_card(
        self,
        origin: str,
        card: dict[str, Any],
        client: Any = None,
        session_id: str = "",
        event_id: str | None = None,
        msg_id: str | None = None,
        initiator_openid: str = "",
        clicker_header: str | None = None,
    ) -> str:
        """Send a one-off card. Public API for flow/game plugins.

        Returns the session id so a plugin can retire the whole flow later via
        :meth:`end_ephemeral_session`.

        ``clicker_header`` defaults to an automatically derived "👤 name" line
        for ``initiator_openid``. Callers do not have to know the feature
        exists -- a game plugin that simply passes ``initiator_openid`` gets the
        header for free. Pass ``""`` to suppress it explicitly.
        """
        validated = ephemeral.validate_card(card)
        if clicker_header is None:
            clicker_header = await self._clicker_header(origin, initiator_openid)
        # "仅发起者可用" only has meaning when a click triggered this send.
        validated = ephemeral.bind_initiator(validated, initiator_openid)
        client = client or self._get_qq_client(origin)
        nonce, session_id = await self.store.issue_ephemeral_card(
            origin, validated, session_id
        )
        payload: dict[str, Any] = {
            "group_openid": origin.split(":", 2)[-1],
            "msg_type": 2,
            "markdown": {"content": self._prepend_header(
                await self._render_dynamic_markdown(validated["markdown"], origin),
                clicker_header,
            )},
            "keyboard": {"content": {
                "rows": ephemeral.to_keyboard_rows(validated, nonce)
            }},
            "msg_seq": next_msg_seq(),
        }
        msg_id = real_msg_id(msg_id)
        if msg_id:
            payload["msg_id"] = msg_id
        elif event_id:
            payload["event_id"] = event_id
        previous = self._session_messages.get(session_id)
        result = await client.api.post_group_message(**payload)

        sent_id = self._extract_message_id(result)
        if sent_id:
            self._session_messages[session_id] = (sent_id, time.time())
        # One session is one conversation: a board that redraws every turn, or
        # a flow moving to its next step. Leaving every superseded card on
        # screen is what makes these games feel like spam, so retire the one
        # this replaces -- after the new card is up, never before.
        if previous and self.recall_superseded_cards:
            await self._recall_quietly(origin, previous, client)
        return session_id

    @property
    def _session_messages(self) -> dict[str, tuple[str, float]]:
        """session_id -> (message id, sent at). In memory on purpose.

        QQ refuses recalls older than two minutes, so nothing here outlives a
        restart in any useful way and persisting it would only add a file to
        keep consistent.
        """
        table = getattr(self, "_session_message_table", None)
        if table is None:
            table = {}
            self._session_message_table = table
        return table

    @staticmethod
    def _extract_message_id(result: Any) -> str:
        for attr in ("id", "msg_id", "message_id"):
            value = getattr(result, attr, None) or (
                result.get(attr) if isinstance(result, dict) else None
            )
            if value:
                return str(value)
        return ""

    async def _recall_quietly(self, origin: str, previous: tuple[str, float],
                              client: Any = None) -> None:
        """Best-effort tidy-up. Never raises: the new card already went out."""
        message_id, sent_at = previous
        try:
            await self.recall_message(origin, message_id,
                                      client=client, sent_at=sent_at)
        except Exception:
            logger.debug("[QQHub] Failed to recall a superseded card")

    def register_card_provider(self, card_id: str, provider: Any) -> None:
        """Register a builder for a ``next_card`` target.

        The provider receives an :class:`EphemeralContext` and returns a card
        dict (or None to send nothing). This is what lets a static
        ``next_card`` reference produce a freshly-rendered card, including
        loops back to an earlier card id.
        """
        if not ephemeral.CARD_ID_RE.fullmatch(card_id):
            raise ValueError("card_id 含非法字符")
        self._card_providers[card_id] = provider

    def unregister_card_provider(self, card_id: str) -> None:
        self._card_providers.pop(card_id, None)

    async def end_ephemeral_session(self, session_id: str) -> int:
        """Retire every card in a session, e.g. when a match ends."""
        return await self.store.end_ephemeral_session(session_id)
