"""QQ Official Hub AstrBot plugin entry point."""
from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.star.session_llm_manager import SessionServiceManager

from .qqofficial_hub import interaction_bridge
from .qqofficial_hub.action_registry import (
    ActionContext,
    ActionSpec,
    get_action_registry,
)
from .qqofficial_hub.command_catalog import build_command_catalog
from .qqofficial_hub.command_dispatch import (
    dispatch_registered_command,
    passive_event_id,
)
from .qqofficial_hub import push_status
from .qqofficial_hub.passive_reply import next_msg_seq
from .qqofficial_hub.store import PanelStore
from .web import HubWebController

PLUGIN_NAME = "astrbot_plugin_qqofficial_hub"


def _authorize_flag(authorize: object):
    """Best-effort read of an authorize event's boolean, if it carries one.

    The documented payload lists only opt_scene/scope, so real events may or
    may not include an explicit flag. Return None when absent and let the
    caller treat the event itself as a grant.
    """
    for key in ("authorized", "status", "is_authorized", "enable", "enabled"):
        if isinstance(authorize, dict):
            if key in authorize:
                return authorize[key]
        elif hasattr(authorize, key):
            return getattr(authorize, key)
    return None


@register(PLUGIN_NAME, "QQ Official Hub", "QQ 官方机器人 Keyboard 面板与 Interaction 安全中枢。", "0.6.0", "204343414")
class QQOfficialHubPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.context = context
        self.config = config
        raw_operators = config.get("operator_openids", "") or ""
        self.operator_openids = {
            item.strip() for item in re.split(r"[\s,，;；]+", str(raw_operators))
            if item.strip()
        }
        self.callback_ttl_seconds = max(int(config.get("callback_ttl_hours", 24)), 1) * 3600
        self.store = PanelStore(
            StarTools.get_data_dir(PLUGIN_NAME),
            callback_ttl_seconds=self.callback_ttl_seconds,
        )
        self.actions = get_action_registry()
        self.actions.unregister_owner(PLUGIN_NAME)
        self.actions.register(ActionSpec(
            action_id="hub.refresh",
            title="刷新当前面板",
            description="重新读取当前群配置并发送一张新面板",
            owner=PLUGIN_NAME,
            default_permission="everyone",
            callback=self._action_refresh,
        ))
        self.actions.register(ActionSpec(
            action_id="hub.test",
            title="测试后台回调",
            description="ACK 后发送一张新面板，用于验证 Interaction 和点击者 At",
            owner=PLUGIN_NAME,
            default_permission="group_manager",
            callback=self._action_test,
        ))
        self.web = HubWebController(context, self.store, self)
        self.web.register_routes()
        self.experimental_bridge = bool(config.get("experimental_interaction_bridge", False))
        self.empty_mention_opens_panel = bool(config.get("empty_mention_opens_panel", True))
        push_cfg = config.get("push_status_display", {}) or {}
        self.push_lamps = {
            push_status.GRANTED: str(push_cfg.get("lamp_granted", "") or "").strip(),
            push_status.REVOKED: str(push_cfg.get("lamp_revoked", "") or "").strip(),
            push_status.UNKNOWN: str(push_cfg.get("lamp_unknown", "") or "").strip(),
        }
        self.push_templates = {
            push_status.GRANTED: str(push_cfg.get("text_granted", "") or "").strip(),
            push_status.REVOKED: str(push_cfg.get("text_revoked", "") or "").strip(),
            push_status.UNKNOWN: str(push_cfg.get("text_unknown", "") or "").strip(),
        }
        self.bridge_generation: int | None = None
        if self.experimental_bridge:
            self.bridge_generation = interaction_bridge.install(PLUGIN_NAME, self._handle_interaction)

    async def initialize(self) -> None:
        if self.experimental_bridge:
            logger.warning("[QQHub] Experimental callback test is enabled. Use only after a full AstrBot restart.")
        else:
            logger.info("[QQHub] Editor loaded. Experimental callback bridge is disabled.")

    async def terminate(self) -> None:
        self.actions.unregister_owner(PLUGIN_NAME)
        self.actions.unregister_owner(f"{PLUGIN_NAME}.commands")
        if self.experimental_bridge:
            interaction_bridge.detach(PLUGIN_NAME)

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def observe_qqofficial_group(self, event: AstrMessageEvent):
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        if "GroupMessage" not in origin:
            return
        platform_id = origin.split(":", 1)[0]
        platform = self.context.get_platform_inst(platform_id)
        if platform is not None and platform.meta().name == "qq_official":
            await self.store.observe_group(origin, platform_id)

    @filter.event_message_type(filter.EventMessageType.ALL, priority=100)
    async def show_panel_hint_when_llm_disabled(self, event: AstrMessageEvent):
        """Last-resort hint for an otherwise unhandled QQ Official wake-up."""
        if not event.is_at_or_wake_command:
            return
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        platform_id = origin.split(":", 1)[0] if origin else str(event.get_platform_id() or "")
        platform = self.context.get_platform_inst(platform_id)
        if platform is None or platform.meta().name != "qq_official":
            return
        # Slash commands belong to AstrBot/plugin routing even when their
        # handler does not call stop_event(). WakingCheck may already have
        # removed the wake prefix from event.message_str, so inspect the
        # immutable AstrBotMessage copy as well. Synthetic Type 1 commands are
        # explicitly marked because their raw Interaction has no text content.
        if event.get_extra("qqhub_synthetic_command", False):
            return
        original_text = str(
            getattr(getattr(event, "message_obj", None), "message_str", "") or ""
        )
        current_text = str(event.get_message_str() or "")
        if original_text.lstrip().startswith("/") or current_text.lstrip().startswith("/"):
            return

        config = self.context.get_config(umo=origin)
        globally_enabled = bool(
            config.get("provider_settings", {}).get("enable", True)
        )
        session_enabled = await SessionServiceManager.is_llm_enabled_for_session(
            origin
        )
        if globally_enabled and session_enabled:
            return

        event.stop_event()
        if "GroupMessage" not in origin:
            # Do not send Hub cards in C2C/private chat yet. Private QQ users
            # cannot @ the bot like a group mention; when normal LLM is disabled,
            # give one short routing hint instead of opening a half-supported
            # private panel.
            yield event.plain_result("请在群聊中 @我 输入 /qqhub 查看功能。")
            return

        if not original_text.strip() and not current_text.strip() and self.empty_mention_opens_panel:
            async for result in self._send_panel_from_event(event, command_name="@bot"):
                yield result
            return
        if self.empty_mention_opens_panel:
            async for result in self._send_panel_from_event(event, command_name="@bot"):
                yield result
        else:
            yield event.plain_result("请@我输入 /qqhub 查看功能")

    async def _send_panel_from_event(self, event: AstrMessageEvent, command_name: str = "/qqhub"):
        # This is a control command, never an LLM prompt. AstrBot continues the
        # pipeline unless a command handler explicitly stops the event.
        origin = str(event.unified_msg_origin or "")
        if "GroupMessage" not in origin:
            yield event.plain_result(f"{command_name} 目前仅支持 QQ Official 群聊。")
            return
        if not self.experimental_bridge:
            yield event.plain_result("测试卡尚未启用。请在 Hub 配置中开启「实验性 QQ Interaction 测试桥」，然后完整重启 AstrBot。")
            return
        try:
            await self._send_configured_panel(origin, msg_id=str(event.message_obj.message_id))
        except Exception as exc:
            logger.exception("[QQHub] Failed to send panel")
            yield event.plain_result(f"测试卡发送失败：{type(exc).__name__}: {exc}")

    @filter.command("qqhub", priority=100)
    async def send_default_panel(self, event: AstrMessageEvent):
        """发送当前群配置的 QQ 官方 Hub 面板。"""
        event.stop_event()
        async for result in self._send_panel_from_event(event, command_name="/qqhub"):
            yield result

    @filter.command("qqhub 面板", alias={"qqhub panel"}, priority=100)
    async def send_default_panel_legacy(self, event: AstrMessageEvent):
        """兼容旧入口：发送当前群配置的 QQ 官方 Hub 面板。"""
        event.stop_event()
        async for result in self._send_panel_from_event(event, command_name="/qqhub 面板"):
            yield result

    @filter.command("qqhub 艾特回复测试", priority=100)
    async def mention_reply_probe(self, event: AstrMessageEvent):
        """Type 2/typed-command probe: one native text reply that At's sender."""
        event.stop_event()
        try:
            await self._send_native_mention_probe(event, reply=True)
        except Exception as exc:
            logger.exception("[QQHub] Native mention reply probe failed")
            yield event.plain_result(f"艾特回复测试失败：{type(exc).__name__}: {exc}")

    @filter.command("qqhub 艾特主动测试", priority=100)
    async def mention_proactive_probe(self, event: AstrMessageEvent):
        """Type 2/typed-command probe: one proactive native text At."""
        event.stop_event()
        try:
            await self._send_native_mention_probe(event, reply=False)
        except Exception as exc:
            logger.exception("[QQHub] Native mention proactive probe failed")
            yield event.plain_result(f"艾特主动测试失败：{type(exc).__name__}: {exc}")

    async def _send_native_mention_probe(
        self, event: AstrMessageEvent, *, reply: bool
    ) -> None:
        origin = str(event.unified_msg_origin or "")
        if "GroupMessage" not in origin:
            raise ValueError("该测试仅支持 QQ Official 群聊")
        client = self._get_qq_client(origin)
        member_openid = str(event.get_sender_id() or "").strip()
        if not member_openid:
            raise ValueError("当前消息没有 group_member_openid")
        payload: dict[str, Any] = {
            "group_openid": origin.split(":", 2)[-1],
            "msg_type": 0,
            # The documented latest qqbot-at-user form is currently escaped
            # literally by the real group text endpoint/client. Probe QQ's
            # deprecated-but-still-documented native text-chain form instead.
            "content": (
                f'<@{member_openid}> '
                f'{"被动回复" if reply else "主动消息"}旧协议艾特测试成功'
            ),
            "msg_seq": next_msg_seq(),
        }
        if reply:
            msg_id = str(event.message_obj.message_id or "").strip()
            if not msg_id:
                raise ValueError("当前消息没有可用于回复的 msg_id")
            payload["msg_id"] = msg_id
        await client.api.post_group_message(**payload)
        logger.info(
            "[QQHub] Native mention probe sent mode=%s group=%s member=%s",
            "reply" if reply else "proactive",
            payload["group_openid"],
            member_openid[-8:],
        )

    @staticmethod
    def _command_action_id(command: str) -> str:
        digest = hashlib.sha256(command.encode("utf-8")).hexdigest()[:16]
        return f"command.{digest}"

    def _sync_command_actions(self) -> None:
        owner = f"{PLUGIN_NAME}.commands"
        self.actions.unregister_owner(owner)
        for item in build_command_catalog(self.context):
            command = str(item["command"])

            async def callback(
                context: ActionContext,
                params: dict[str, Any],
                command_text: str = command,
            ) -> int:
                arguments = str(params.get("arguments", "") or "").strip()
                if len(arguments) > 100:
                    return 1
                full_command = command_text + (f" {arguments}" if arguments else "")
                dispatch_registered_command(
                    context.client,
                    context.interaction,
                    full_command,
                    mention_openid=(
                        context.member_openid
                        if context.mention_clicker
                        else ""
                    ),
                )
                return 0

            self.actions.register(ActionSpec(
                action_id=self._command_action_id(command),
                title=f"直接执行 {command}",
                description=(
                    f"通过 AstrBot 正常命令流水线执行；参数放在 arguments。"
                    f" {item.get('description', '')}"
                ).strip(),
                owner=owner,
                default_permission=(
                    "astrbot_admin"
                    if item.get("permission") == "admin"
                    else "everyone"
                ),
                callback=callback,
            ))

    def get_action_catalog(self) -> list[dict[str, str]]:
        """Only registered, implemented callbacks may be selected by the UI."""
        self._sync_command_actions()
        return self.actions.catalog()

    def validate_registered_actions(self, panel: object) -> None:
        if not isinstance(panel, dict):
            return
        allowed = {item["id"] for item in self.get_action_catalog()}
        for row in panel.get("rows", []):
            if not isinstance(row, list):
                continue
            for button in row:
                if (
                    isinstance(button, dict)
                    and int(button.get("action_type", -1)) == 1
                    and str(button.get("data", "")) not in allowed
                ):
                    raise ValueError(
                        f"后台功能未注册: {button.get('data', '')}"
                    )

    async def send_panel_from_ui(self, origin: str) -> dict[str, Any]:
        origin = str(origin or "")
        if "GroupMessage" not in origin:
            raise ValueError("测试目标必须是已观察到的群会话")
        snapshot = await self.store.bootstrap()
        panel = snapshot["group_overrides"].get(origin) or snapshot["templates"]["default_panel"]
        if not self.experimental_bridge and any(
            int(button.get("action_type", -1)) == 1
            for row in panel.get("rows", []) for button in row
        ):
            raise ValueError("当前卡片包含后台回调按钮；请启用 Interaction 兼容桥并完整重启后再测试")
        await self._send_configured_panel(origin)
        return {"sent": True, "origin": origin}

    def _get_qq_client(self, origin: str):
        platform_id = origin.split(":", 1)[0]
        adapter = self.context.get_platform_inst(platform_id)
        if adapter is None or adapter.meta().name != "qq_official":
            raise ValueError("目标不是已加载的 QQ Official 群")
        client = adapter.get_client() if hasattr(adapter, "get_client") else getattr(adapter, "client", None)
        if client is None or getattr(client, "api", None) is None:
            raise RuntimeError("无法取得 QQ Official botpy client")
        return client

    @staticmethod
    def _permission_payload(button: dict[str, Any]) -> dict[str, Any]:
        policy = str(button.get("permission", ""))
        if policy == "specified_users":
            return {"type": 0, "specify_user_ids": list(button.get("specified_users", []))}
        if policy == "group_manager":
            return {"type": 1}
        # AstrBot-admin/operator are verified by Hub after a callback. QQ has
        # no equivalent policy field, so it must allow the click through.
        return {"type": 2}

    @classmethod
    def _button(cls, button: dict[str, Any], nonce: str) -> dict[str, Any]:
        action_type = int(button["action_type"])
        data = str(button["data"])
        if action_type == 1:
            data = f"qqhub:v2:{nonce}:{button['id']}"
        return {
            "id": str(button["id"]),
            "render_data": {"label": button["label"], "visited_label": button["visited_label"], "style": int(button["style"])},
            "action": {
                "type": action_type,
                "permission": cls._permission_payload(button),
                "data": data,
                "reply": bool(button.get("reply", False)),
                "enter": bool(button.get("enter", False)),
                "anchor": int(button.get("anchor", 0) or 0),
                "unsupport_tips": str(button.get("unsupport_tips") or "当前 QQ 版本不支持该按钮"),
            },
        }

    async def _render_dynamic_markdown(self, markdown: str, origin: str) -> str:
        """Resolve editor-inserted placeholders just before sending.

        Placeholders are opt-in: a card that never inserted one is returned
        untouched, so blueprint/board-game cards pay nothing for this.
        """
        if "{{" not in markdown:
            return markdown
        if push_status.has_placeholder(markdown):
            markdown = push_status.render(
                markdown,
                await self.store.get_push_state(origin),
                lamps=self.push_lamps,
                templates=self.push_templates,
            )
        if "{{group_openid_short}}" in markdown:
            markdown = markdown.replace(
                "{{group_openid_short}}", origin.split(":", 2)[-1][-8:]
            )
        return markdown

    async def _send_configured_panel(
        self,
        origin: str,
        client=None,
        msg_id: str | None = None,
        event_id: str | None = None,
        mention_openid: str = "",
    ) -> None:
        client = client or self._get_qq_client(origin)
        snapshot = await self.store.bootstrap()
        panel = snapshot["group_overrides"].get(origin) or snapshot["templates"]["default_panel"]
        nonce = await self.store.issue_panel_card(origin, panel, reply_msg_id=msg_id)
        rows = [{"buttons": [self._button(button, nonce) for button in row]} for row in panel["rows"]]
        markdown_content = await self._render_dynamic_markdown(str(panel["markdown"]), origin)
        # Real-device tests show both documented and legacy At tags are exposed
        # literally on this QQ group path. Keep the setting/card metadata for a
        # future native implementation, but never contaminate visible output.
        payload = {
            "group_openid": origin.split(":", 2)[-1],
            "msg_type": 2,
            "markdown": {"content": markdown_content},
            "keyboard": {"content": {"rows": rows}},
            "msg_seq": next_msg_seq(),
        }
        if msg_id:
            payload["msg_id"] = msg_id
        elif event_id:
            payload["event_id"] = event_id
        proactive = not msg_id and not event_id
        try:
            await client.api.post_group_message(**payload)
        except Exception:
            # A rejected *proactive* send is hard evidence that push is off.
            # Passive sends say nothing about push, so never infer from them.
            if proactive:
                await self.store.set_push_state(origin, push_status.REVOKED, "send")
            raise
        if proactive:
            await self.store.set_push_state(origin, push_status.GRANTED, "send")
        logger.info("[QQHub] Configured panel sent to %s revision=%s", origin, panel.get("revision"))

    async def _action_refresh(
        self, context: ActionContext, params: dict[str, Any]
    ) -> int:
        # INTERACTION_CREATE carries an event id that QQ accepts as a passive
        # reply credential (docs: send.html event_id supports
        # "INTERACTION_CREATE"). Passing it keeps the button reply a *passive*
        # message, so it needs neither the proactive-push permission nor the
        # monthly 4-message proactive quota.
        #
        # Await the send instead of fire-and-forget: the ACK code must reflect
        # the real outcome. Returning 0 while the send failed made the client
        # show success and hid a genuine bug behind a log line.
        try:
            await self._send_configured_panel(
                context.origin,
                client=context.client,
                event_id=passive_event_id(context.interaction) or None,
                mention_openid=context.member_openid,
            )
        except Exception:
            logger.exception("[QQHub] Whiteboard refresh failed")
            return 1
        return 0

    async def _action_test(
        self, context: ActionContext, params: dict[str, Any]
    ) -> int:
        # The harmless test currently behaves like refresh; params are accepted
        # to prove structured Action plumbing without executing arbitrary code.
        return await self._action_refresh(context, params)

    def _is_astrbot_admin_openid(self, member_openid: str, origin: str) -> bool:
        try:
            config = self.context.get_config(umo=origin)
            admins = config.get("admins_id", []) or []
            return member_openid in {str(item) for item in admins}
        except Exception as exc:
            logger.warning("[QQHub] Cannot read AstrBot admins_id: %s", exc)
            return False

    async def _handle_authorize_event(self, interaction: Any) -> int:
        """Record type=18/19 authorize events.

        ``authorize_data.scope`` tells us whether proactive push is granted
        (``group_push`` / ``c2c_push``). Persisting it turns "does this group
        allow proactive messages?" from a blind retry into a known fact.
        Docs say these types need no ACK, so the code is informational only.
        """
        resolved = getattr(getattr(interaction, "data", None), "resolved", None)
        authorize = getattr(resolved, "authorize_data", None)
        scope = str(getattr(authorize, "scope", "") or "")
        if not scope and isinstance(authorize, dict):
            scope = str(authorize.get("scope", "") or "")
        opt_scene = str(getattr(authorize, "opt_scene", "") or "")
        if not opt_scene and isinstance(authorize, dict):
            opt_scene = str(authorize.get("opt_scene", "") or "")
        group_openid = str(getattr(interaction, "group_openid", "") or "")
        user_openid = str(getattr(interaction, "user_openid", "") or "")
        try:
            await self.store.record_authorization(
                platform_id=self._platform_id_of(None),
                group_openid=group_openid,
                user_openid=user_openid,
                scope=scope,
                opt_scene=opt_scene,
            )
        except Exception as exc:
            logger.warning("[QQHub] Cannot persist authorization: %s", exc)
        state = push_status.state_from_authorize_event(
            scope,
            _authorize_flag(authorize),
            is_group=bool(group_openid),
        )
        if state and group_openid:
            origin = f"{self._platform_id_of(None)}:GroupMessage:{group_openid}"
            await self.store.set_push_state(origin, state, "authorize")
        logger.info(
            "[QQHub] Authorize event scope=%s scene=%s group=%s user=%s state=%s",
            scope or "?", opt_scene or "?",
            group_openid[-8:] if group_openid else "-",
            user_openid[-8:] if user_openid else "-",
            state or "n/a",
        )
        return 0

    @staticmethod
    def _platform_id_of(client: Any) -> str:
        try:
            return str(client.platform.meta().id)
        except Exception:
            return "qq_official"

    async def _handle_interaction(self, client: Any, interaction: Any) -> int:
        interaction_type = getattr(interaction, "type", None)
        if interaction_type in (18, 19):
            return await self._handle_authorize_event(interaction)

        resolved = getattr(getattr(interaction, "data", None), "resolved", None)
        data = str(getattr(resolved, "button_data", "") or "")
        parts = data.split(":", 3)
        if len(parts) != 4 or parts[0] != "qqhub" or parts[1] != "v2":
            if interaction_type == 12:
                # Quick menus are configured in QQ's admin console and carry a
                # feature_id instead of our button_data. Nothing is registered
                # for them yet, so report "operation failed" rather than
                # pretending success.
                logger.info(
                    "[QQHub] Unhandled quick-menu feature_id=%s",
                    str(getattr(resolved, "feature_id", "") or "?"),
                )
            return 1
        _, _, nonce, button_id = parts
        group_openid = str(getattr(interaction, "group_openid", "") or "")
        user_openid = str(getattr(interaction, "user_openid", "") or "")
        if not group_openid and user_openid:
            # C2C buttons are delivered without group context. Hub panels are
            # issued per group, so a single-chat click cannot match a stored
            # card; refuse explicitly instead of a generic failure.
            logger.info("[QQHub] C2C button click is not supported yet")
            return 4
        if not group_openid:
            return 1
        origin = f"{client.platform.meta().id}:GroupMessage:{group_openid}"
        issued = await self.store.get_issued_button_context(origin, nonce, button_id)
        if issued is None:
            logger.warning("[QQHub] Rejected stale/cross-group callback button=%s", button_id)
            return 3
        button, _reply_msg_id, mention_clicker = issued
        member = str(getattr(interaction, "group_member_openid", "") or "")
        policy = button["permission"]
        if policy == "specified_users" and member not in button["specified_users"]:
            return 4
        # group_manager is enforced by QQ before callback delivery. Policies
        # without a QQ-native equivalent are verified here using OpenID.
        if policy == "astrbot_admin" and not self._is_astrbot_admin_openid(member, origin):
            return 4
        if policy == "operator" and member not in self.operator_openids:
            return 4
        action_id = str(button["data"])
        params = button.get("action_params", {})
        if not isinstance(params, dict):
            return 1
        logger.info(
            "[QQHub] Callback action=%s group=%s member=%s",
            action_id, group_openid, member[-8:],
        )
        self._sync_command_actions()
        context = ActionContext(
            client=client,
            interaction=interaction,
            origin=origin,
            group_openid=group_openid,
            member_openid=member,
            mention_clicker=mention_clicker,
        )
        return await self.actions.execute(action_id, context, params)

