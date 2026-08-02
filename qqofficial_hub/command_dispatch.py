"""Dispatch a registered command through AstrBot's normal event pipeline."""
from __future__ import annotations

import random
from typing import Any

from astrbot.api import logger

from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import At, Plain
from astrbot.api.platform import AstrBotMessage, MessageMember, MessageType


class HubSyntheticCommandEvent(AstrMessageEvent):
    """QQ group event whose replies are sent proactively, without fake msg_id."""

    def __init__(
        self,
        command: str,
        adapter: Any,
        client: Any,
        interaction: Any,
        mention_openid: str = "",
    ):
        group_openid = str(getattr(interaction, "group_openid", "") or "")
        member_openid = str(
            getattr(interaction, "group_member_openid", "") or ""
        )
        message = AstrBotMessage()
        message.type = MessageType.GROUP_MESSAGE
        message.self_id = "qq_official"
        message.session_id = group_openid
        message.message_id = f"hub-interaction:{getattr(interaction, 'id', '')}"
        message.group_id = group_openid
        message.sender = MessageMember(member_openid, "")
        message.message_str = command
        message.message = [At(qq="qq_official"), Plain(command)]
        message.raw_message = interaction
        super().__init__(command, message, adapter.meta(), group_openid)
        self.bot = client
        self._adapter = adapter
        self._mention_openid = mention_openid
        self._group_openid = group_openid
        # INTERACTION_CREATE.id is accepted by QQ as a passive-reply credential
        # (docs: send.html, event_id supports "INTERACTION_CREATE"). Using it
        # keeps button replies passive: no proactive-push permission needed and
        # no monthly 4-message proactive quota consumed.
        self._event_id = str(getattr(interaction, "id", "") or "")
        self._event_id_used = False
        self.set_extra("qqhub_synthetic_command", True)

    async def send(self, message: MessageChain) -> None:
        # Do not try to prepend <qqbot-at-user .../> here. Field testing on
        # QQ Official group messages shows:
        #
        # 1. type=2 command buttons are client-side command input. Whether the
        #    incoming command event is displayed as [At:qq_official] or
        #    [At:<real bot id>] depends on QQ/AstrBot receive mode: when the
        #    bot only receives @ messages, QQ tends to synthesize the @ context;
        #    when the bot receives all group messages, the full mention object
        #    may be absent/different. This is not a reliable clicker identity.
        # 2. type=1 callback buttons do provide group_member_openid in the
        #    Interaction event, but require the group owner to enable proactive
        #    message push. Current QQ group active-message paths do not reliably
        #    render either documented <qqbot-at-user id="..." /> or legacy
        #    <@openid> as a real @; they may be shown literally.
        #
        # Therefore attribution for type=1 should be kept in server-side audit
        # data (ActionContext.member_openid / logs) unless a later QQ official
        # capability provides a verified native @ path for group active replies.
        await super().send(message)
        if await self._send_as_passive_reply(message):
            return
        await self._adapter.send_by_session(self.session, message)

    async def _send_as_passive_reply(self, message: MessageChain) -> bool:
        """Reply using INTERACTION_CREATE's event_id instead of a proactive push.

        Returns False so the caller can fall back to the proactive path when the
        event id is missing, already spent, or rejected by QQ.
        """
        if not self._event_id or self._event_id_used or not self._group_openid:
            return False
        text = "".join(
            str(getattr(part, "text", "") or "")
            for part in (message.chain or [])
            if part.__class__.__name__ == "Plain"
        ).strip()
        if not text:
            return False
        try:
            await self.bot.api.post_group_message(
                group_openid=self._group_openid,
                msg_type=0,
                content=text,
                event_id=self._event_id,
                msg_seq=random.randint(1, 10000),
            )
        except Exception as exc:
            logger.warning(
                "[QQHub] Passive reply via event_id failed, falling back to "
                "proactive send: %s",
                exc,
            )
            return False
        self._event_id_used = True
        return True

    async def send_streaming(self, generator, use_fallback: bool = False):
        # Group commands should normally be non-streaming. If one streams,
        # merge its chunks into one final active message to preserve Hub's
        # one-operation/one-message policy.
        await super().send_streaming(generator, use_fallback)
        merged = []
        async for chain in generator:
            if chain.type == "break":
                continue
            merged.extend(chain.chain)
        if merged:
            await self.send(MessageChain(chain=merged))


def dispatch_registered_command(
    client: Any,
    interaction: Any,
    command: str,
    mention_openid: str = "",
) -> None:
    adapter = getattr(client, "platform", None)
    if adapter is None:
        raise RuntimeError("QQ client has no bound AstrBot platform")
    event = HubSyntheticCommandEvent(
        command,
        adapter,
        client,
        interaction,
        mention_openid=mention_openid,
    )
    adapter.commit_event(event)
