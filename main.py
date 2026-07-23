"""QQ Official Hub AstrBot plugin entry point.

The first runtime command is intentionally harmless. Native QQ callback
buttons are enabled only after the matching AstrBot adapter support is merged
and installed; this plugin never monkey-patches the adapter.
"""
from __future__ import annotations

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register


@register(
    "astrbot_plugin_qqofficial_hub",
    "QQ Official Hub",
    "QQ 官方机器人 Keyboard 面板与 Interaction 安全中枢。",
    "0.1.0",
    "204343414",
)
class QQOfficialHubPlugin(Star):
    """Entry point for the QQ Official Hub plugin."""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.context = context
        self.config = config

    async def initialize(self) -> None:
        logger.info("[QQOfficialHub] Loaded in safe bootstrap mode.")

    @filter.command("头条卡片", desc="发送本群的 QQ Official 快捷按钮卡片")
    async def send_default_panel(self, event: AstrMessageEvent):
        """Reserve the public command without fabricating unsupported buttons."""
        origin = str(event.unified_msg_origin or "")
        if "GroupMessage" not in origin:
            yield event.plain_result("头条卡片目前仅支持 QQ Official 群聊。")
            return
        yield event.plain_result(
            "QQ Official Hub 已安装，但本群卡片尚未配置。"
            "\n当前版本正在等待 QQ Official Interaction 适配器支持；"
            "不会以不安全的方式模拟后台按钮。"
        )
