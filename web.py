from __future__ import annotations

from typing import Any

from astrbot.api.web import error_response, json_response, request
from astrbot.api.star import Context

from .qqofficial_hub.store import PanelStore

PLUGIN_NAME = "astrbot_plugin_qqofficial_hub"


class HubWebController:
    def __init__(self, context: Context, store: PanelStore) -> None:
        self.context = context
        self.store = store

    def register_routes(self) -> None:
        self.context.register_web_api(f"/{PLUGIN_NAME}/bootstrap", self.bootstrap, ["GET"], "QQ Hub editor bootstrap")
        self.context.register_web_api(f"/{PLUGIN_NAME}/panel", self.save_panel, ["POST"], "Save QQ Hub panel")

    async def bootstrap(self):
        return json_response(await self.store.bootstrap())

    async def save_panel(self):
        payload: dict[str, Any] = await request.json(default={})
        try:
            panel = await self.store.save_panel(
                str(payload.get("scope", "")), str(payload.get("origin", "")), payload.get("panel")
            )
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        return json_response({"panel": panel})
