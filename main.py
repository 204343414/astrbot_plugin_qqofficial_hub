"""QQ Official Hub AstrBot plugin entry point."""
from __future__ import annotations

import asyncio
import random
import re
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .qqofficial_hub import interaction_bridge
from .qqofficial_hub.store import PanelStore
from .web import HubWebController

PLUGIN_NAME = "astrbot_plugin_qqofficial_hub"


@register(PLUGIN_NAME, "QQ Official Hub", "QQ 官方机器人 Keyboard 面板与 Interaction 安全中枢。", "0.2.0", "204343414")
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
        self.web = HubWebController(context, self.store, self)
        self.web.register_routes()
        self.experimental_bridge = bool(config.get("experimental_interaction_bridge", False))
        self.bridge_generation: int | None = None
        if self.experimental_bridge:
            self.bridge_generation = interaction_bridge.install(PLUGIN_NAME, self._handle_interaction)

    async def initialize(self) -> None:
        if self.experimental_bridge:
            logger.warning("[QQHub] Experimental callback test is enabled. Use only after a full AstrBot restart.")
        else:
            logger.info("[QQHub] Editor loaded. Experimental callback bridge is disabled.")

    async def terminate(self) -> None:
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

    @filter.command_group("qqhub")
    def qqhub(self):
        pass

    @qqhub.command("面板")
    async def send_default_panel(self, event: AstrMessageEvent):
        # This is a control command, never an LLM prompt. AstrBot continues the
        # pipeline unless a command handler explicitly stops the event.
        event.stop_event()
        origin = str(event.unified_msg_origin or "")
        if "GroupMessage" not in origin:
            yield event.plain_result("/qqhub 面板 目前仅支持 QQ Official 群聊。")
            return
        if not self.experimental_bridge:
            yield event.plain_result("测试卡尚未启用。请在 Hub 配置中开启「实验性 QQ Interaction 测试桥」，然后完整重启 AstrBot。")
            return
        try:
            await self._send_configured_panel(origin, msg_id=str(event.message_obj.message_id))
        except Exception as exc:
            logger.exception("[QQHub] Failed to send whiteboard")
            yield event.plain_result(f"测试卡发送失败：{type(exc).__name__}: {exc}")

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
        markdown_content = str(panel["markdown"])
        if mention_openid and panel.get("mention_clicker"):
            markdown_content = f"<@{mention_openid}>\n{markdown_content}"
        payload = {
            "group_openid": origin.split(":", 2)[-1],
            "msg_type": 2,
            "markdown": {"content": markdown_content},
            "keyboard": {"content": {"rows": rows}},
            "msg_seq": random.randint(1, 10000),
        }
        if msg_id:
            payload["msg_id"] = msg_id
        elif event_id:
            payload["event_id"] = event_id
        await client.api.post_group_message(**payload)
        logger.info("[QQHub] Configured panel sent to %s revision=%s", origin, panel.get("revision"))

    def _is_astrbot_admin_openid(self, member_openid: str, origin: str) -> bool:
        try:
            config = self.context.get_config(umo=origin)
            admins = config.get("admins_id", []) or []
            return member_openid in {str(item) for item in admins}
        except Exception as exc:
            logger.warning("[QQHub] Cannot read AstrBot admins_id: %s", exc)
            return False

    async def _handle_interaction(self, client: Any, interaction: Any) -> int:
        resolved = getattr(getattr(interaction, "data", None), "resolved", None)
        data = str(getattr(resolved, "button_data", "") or "")
        parts = data.split(":", 3)
        if len(parts) != 4 or parts[0] != "qqhub" or parts[1] != "v2":
            return 1
        _, _, nonce, button_id = parts
        group_openid = str(getattr(interaction, "group_openid", "") or "")
        if not group_openid:
            return 1
        origin = f"{client.platform.meta().id}:GroupMessage:{group_openid}"
        issued = await self.store.get_issued_button_context(origin, nonce, button_id)
        if issued is None:
            logger.warning("[QQHub] Rejected stale/cross-group callback button=%s", button_id)
            return 3
        button, _reply_msg_id = issued
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
        action = str(button["data"])
        logger.info("[QQHub] Callback action=%s group=%s member=%s", action, group_openid, member[-8:])
        if action == "hub.refresh":
            # Callback ACK is separate from chat output. Send a fresh active panel;
            # reusing the original msg_id would expire after QQ's passive window.
            task = asyncio.create_task(
                self._send_configured_panel(
                    origin, client=client, mention_openid=member
                )
            )
            task.add_done_callback(self._log_refresh_failure)
        elif action == "hub.test":
            task = asyncio.create_task(
                self._send_configured_panel(
                    origin, client=client, mention_openid=member
                )
            )
            task.add_done_callback(self._log_refresh_failure)
        else:
            return 1
        return 0

    @staticmethod
    def _log_refresh_failure(task: asyncio.Task) -> None:
        try:
            task.result()
        except Exception:
            logger.exception("[QQHub] Whiteboard refresh task failed")
