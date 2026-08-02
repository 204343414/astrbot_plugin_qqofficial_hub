"""Ephemeral-card routing: click handling, sending and provider registry.

Split out of ``main.py`` so the plugin class stays about lifecycle, commands
and configuration. See ``docs/EPHEMERAL_CARDS.md`` for the contract.
"""
from __future__ import annotations

from typing import Any

from astrbot.api import logger

from .action_registry import ActionContext, EphemeralContext
from . import ephemeral
from .passive_reply import next_msg_seq, passive_event_id


class EphemeralCardMixin:
    """One-shot cards for flows and games."""

    async def _handle_ephemeral_click(
        self, client: Any, interaction: Any, nonce: str, button_id: str
    ) -> int:
        group_openid = str(getattr(interaction, "group_openid", "") or "")
        if not group_openid:
            return ephemeral.CODE_FAILED
        origin = f"{client.platform.meta().id}:GroupMessage:{group_openid}"
        member = str(getattr(interaction, "group_member_openid", "") or "")
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

    async def send_ephemeral_card(
        self,
        origin: str,
        card: dict[str, Any],
        client: Any = None,
        session_id: str = "",
        event_id: str | None = None,
        msg_id: str | None = None,
        initiator_openid: str = "",
    ) -> str:
        """Send a one-off card. Public API for flow/game plugins.

        Returns the session id so a plugin can retire the whole flow later via
        :meth:`end_ephemeral_session`.
        """
        validated = ephemeral.validate_card(card)
        # "仅发起者可用" only has meaning when a click triggered this send.
        validated = ephemeral.bind_initiator(validated, initiator_openid)
        client = client or self._get_qq_client(origin)
        nonce, session_id = await self.store.issue_ephemeral_card(
            origin, validated, session_id
        )
        payload: dict[str, Any] = {
            "group_openid": origin.split(":", 2)[-1],
            "msg_type": 2,
            "markdown": {"content": await self._render_dynamic_markdown(
                validated["markdown"], origin
            )},
            "keyboard": {"content": {
                "rows": ephemeral.to_keyboard_rows(validated, nonce)
            }},
            "msg_seq": next_msg_seq(),
        }
        if msg_id:
            payload["msg_id"] = msg_id
        elif event_id:
            payload["event_id"] = event_id
        await client.api.post_group_message(**payload)
        return session_id

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
