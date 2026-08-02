"""Convert an editor panel into an ephemeral card.

The editor writes one document, but two send paths consume it:

* the **configured panel** (one per group, invalidated by ``revision``);
* an **ephemeral card** (one-shot / ownership / flow).

Only type=1 callback buttons survive the conversion, because an ephemeral card
must route every click back to the server to enforce one-shot and ownership.
URL and command-input buttons cannot do that, so they are dropped rather than
silently behaving differently from what the editor previewed.
"""
from __future__ import annotations

from typing import Any


def panel_to_ephemeral(panel: dict[str, Any]) -> dict[str, Any]:
    rows: list[list[dict[str, Any]]] = []
    for row in panel.get("rows", []):
        built = []
        for button in row:
            if int(button.get("action_type", -1)) != 1:
                continue
            built.append({
                "id": button["id"],
                "label": button["label"],
                "visited_label": button.get("visited_label") or button["label"],
                "style": int(button.get("style", 0)),
                "action_id": button["data"],
                "params": dict(button.get("action_params") or {}),
                "one_shot": bool(button.get("one_shot", False)),
                "owner_mode": str(button.get("owner_mode") or "everyone"),
                "owner_openid": str(button.get("owner_openid") or ""),
                "unsupport_tips": button.get("unsupport_tips") or "当前 QQ 版本不支持该按钮",
            })
        if built:
            rows.append(built)
    return {
        "id": "editor_preview",
        "markdown": panel["markdown"],
        "rows": rows,
        "one_shot": bool(panel.get("one_shot", False)),
        "owner_mode": str(panel.get("owner_mode") or "everyone"),
        "owner_openid": str(panel.get("owner_openid") or ""),
        "owner_reject_tip": str(panel.get("owner_reject_tip") or ""),
    }
