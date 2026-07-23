"""QQ Official Hub AstrBot plugin entry point."""
from __future__ import annotations

import asyncio
import random
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .qqofficial_hub import interaction_bridge
from .qqofficial_hub.store import PanelStore
from .web import HubWebController

PLUGIN_NAME = "astrbot_plugin_qqofficial_hub"


@register(PLUGIN_NAME, "QQ Official Hub", "QQ 官方机器人 Keyboard 面板与 Interaction 安全中枢。", "0.1.0", "204343414")
class QQOfficialHubPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.context = context
        self.config = config
        self.store = PanelStore(StarTools.get_data_dir(PLUGIN_NAME))
        self.web = HubWebController(context, self.store)
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

    @filter.command("头条卡片", desc="发送 QQ Official Hub 白板测试卡")
    async def send_default_panel(self, event: AstrMessageEvent):
        # This is a control command, never an LLM prompt. AstrBot continues the
        # pipeline unless a command handler explicitly stops the event.
        event.stop_event()
        origin = str(event.unified_msg_origin or "")
        if "GroupMessage" not in origin:
            yield event.plain_result("头条卡片目前仅支持 QQ Official 群聊。")
            return
        if not self.experimental_bridge:
            yield event.plain_result("测试卡尚未启用。请在 Hub 配置中开启「实验性 QQ Interaction 测试桥」，然后完整重启 AstrBot。")
            return
        try:
            await self._send_configured_panel(origin, msg_id=str(event.message_obj.message_id))
        except Exception as exc:
            logger.exception("[QQHub] Failed to send whiteboard")
            yield event.plain_result(f"测试卡发送失败：{type(exc).__name__}: {exc}")

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
                "reply": False,
                "enter": False,
                "unsupport_tips": "当前 QQ 版本不支持该按钮",
            },
        }

    async def _send_configured_panel(
        self, origin: str, client=None, msg_id: str | None = None, event_id: str | None = None
    ) -> None:
        client = client or self._get_qq_client(origin)
        snapshot = await self.store.bootstrap()
        panel = snapshot["group_overrides"].get(origin) or snapshot["templates"]["default_panel"]
        nonce = await self.store.issue_panel_card(origin, panel)
        rows = [{"buttons": [self._button(button, nonce) for button in row]} for row in panel["rows"]]
        payload = {
            "group_openid": origin.split(":", 2)[-1],
            "msg_type": 2,
            "markdown": {"content": panel["markdown"]},
            "keyboard": {"content": {"rows": rows}},
            "msg_seq": random.randint(1, 10000),
        }
        if msg_id:
            payload["msg_id"] = msg_id
        elif event_id:
            payload["event_id"] = event_id
        await client.api.post_group_message(**payload)
        logger.info("[QQHub] Configured panel sent to %s revision=%s", origin, panel.get("revision"))

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
        button = await self.store.get_issued_button(origin, nonce, button_id)
        if button is None:
            logger.warning("[QQHub] Rejected stale/cross-group callback button=%s", button_id)
            return 3
        member = str(getattr(interaction, "group_member_openid", "") or "")
        policy = button["permission"]
        if policy == "specified_users" and member not in button["specified_users"]:
            return 4
        # QQ manager permission is platform-enforced. AstrBot-admin/operator
        # are deliberately denied until their authoritative ID source is wired.
        if policy in {"astrbot_admin", "operator"}:
            return 4
        action = str(button["data"])
        logger.info("[QQHub] Callback action=%s group=%s member=%s", action, group_openid, member[-8:])
        if action == "hub.refresh":
            task = asyncio.create_task(
                self._send_configured_panel(origin, client=client, event_id=str(interaction.id))
            )
            task.add_done_callback(self._log_refresh_failure)
        elif action != "hub.test":
            return 1
        return 0

    @staticmethod
    def _log_refresh_failure(task: asyncio.Task) -> None:
        try:
            task.result()
        except Exception:
            logger.exception("[QQHub] Whiteboard refresh task failed")
