"""QQ Official Hub AstrBot plugin entry point."""
from __future__ import annotations

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

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

    async def initialize(self) -> None:
        logger.info("[QQOfficialHub] Editor page and safe bootstrap mode loaded.")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def observe_qqofficial_group(self, event: AstrMessageEvent):
        origin = str(getattr(event, "unified_msg_origin", "") or "")
        if "GroupMessage" not in origin:
            return
        platform_id = origin.split(":", 1)[0]
        platform = self.context.get_platform_inst(platform_id)
        if platform is not None and platform.meta().name == "qq_official":
            await self.store.observe_group(origin, platform_id)

    @filter.command("头条卡片", desc="发送本群的 QQ Official 快捷按钮卡片")
    async def send_default_panel(self, event: AstrMessageEvent):
        origin = str(event.unified_msg_origin or "")
        if "GroupMessage" not in origin:
            yield event.plain_result("头条卡片目前仅支持 QQ Official 群聊。")
            return
        yield event.plain_result("本群已记录到 QQ Official Hub。请在插件详情的「Pages / panels」页面配置卡片。")
