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
        origin = str(event.unified_msg_origin or "")
        if "GroupMessage" not in origin:
            yield event.plain_result("头条卡片目前仅支持 QQ Official 群聊。")
            return
        if not self.experimental_bridge:
            yield event.plain_result("测试卡尚未启用。请在 Hub 配置中开启「实验性 QQ Interaction 测试桥」，然后完整重启 AstrBot。")
            return
        try:
            await self._send_whiteboard(origin)
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
    def _button(button_id: str, label: str, style: int, action_type: int, data: str, permission: int) -> dict[str, Any]:
        return {
            "id": button_id,
            "render_data": {"label": label, "visited_label": label, "style": style},
            "action": {
                "type": action_type,
                "permission": {"type": permission},
                "data": data,
                "reply": False,
                "enter": False,
                "unsupport_tips": "当前 QQ 版本不支持该按钮",
            },
        }

    async def _send_whiteboard(self, origin: str, client=None) -> None:
        client = client or self._get_qq_client(origin)
        nonce = await self.store.issue_test_card(origin)
        action = lambda name: f"qqhub:v1:{nonce}:{name}"
        markdown = {
            "content": (
                "# QQ Official Hub 白板测试卡\n"
                "第一行测试颜色和普通成员后台回调；第二行测试刷新与群管理限制；"
                "第三行测试输入框指令与 URL。测试卡 15 分钟后失效。"
            )
        }
        keyboard = {"content": {"rows": [
            {"buttons": [
                self._button("blue_everyone", "蓝色：所有人可点", 1, 1, action("blue"), 2),
                self._button("gray_everyone", "灰色：所有人可点", 0, 1, action("gray"), 2),
            ]},
            {"buttons": [
                self._button("refresh", "刷新测试卡", 1, 1, action("refresh"), 2),
                self._button("manager", "仅群管理可点", 0, 1, action("manager"), 1),
            ]},
            {"buttons": [
                self._button("insert", "放入输入框，不发送", 0, 2, "/头条卡片", 2),
                self._button("docs", "打开 QQ 按钮文档", 1, 0, "https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/trans/msg-btn.html", 2),
            ]},
        ]}}
        await client.api.post_group_message(
            group_openid=origin.split(":", 2)[-1],
            msg_type=2,
            markdown=markdown,
            keyboard=keyboard,
            msg_seq=random.randint(1, 10000),
        )
        logger.info("[QQHub] Whiteboard test card sent to %s", origin)

    async def _handle_interaction(self, client: Any, interaction: Any) -> int:
        resolved = getattr(getattr(interaction, "data", None), "resolved", None)
        data = str(getattr(resolved, "button_data", "") or "")
        if not data.startswith("qqhub:v1:"):
            return 1
        parts = data.split(":", 3)
        if len(parts) != 4:
            return 1
        _, _, nonce, action = parts
        group_openid = str(getattr(interaction, "group_openid", "") or "")
        if not group_openid:
            return 1
        origin = f"{client.platform.meta().id}:GroupMessage:{group_openid}"
        if not await self.store.claim_test_action(origin, nonce):
            logger.warning("[QQHub] Rejected stale or cross-group test action: %s", action)
            return 3
        member = str(getattr(interaction, "group_member_openid", "") or "")
        logger.info("[QQHub] Whiteboard callback action=%s group=%s member=%s", action, group_openid, member[-8:])
        if action == "refresh":
            task = asyncio.create_task(self._send_whiteboard(origin, client=client))
            task.add_done_callback(self._log_refresh_failure)
        elif action not in {"blue", "gray", "manager"}:
            return 1
        return 0

    @staticmethod
    def _log_refresh_failure(task: asyncio.Task) -> None:
        try:
            task.result()
        except Exception:
            logger.exception("[QQHub] Whiteboard refresh task failed")
