"""Ephemeral cards: short-lived, code-generated panels for flows and games.

Why this exists separately from the configured panel
----------------------------------------------------
The configured panel is *one per group* and is deliberately invalidated by a
``revision`` bump, so a stale card can never execute newly-granted permissions.
That is right for configuration, and fatal for anything dynamic: a game sends a
fresh card every turn, and bumping the group's revision each move would both
pollute group config and let old cards be replayed.

Ephemeral cards therefore live in their own table with their own rules:

* **one-shot** -- a card (or a single button) may declare itself consumable, so
  clicking it once retires it. Without this a player can click the same cell
  twice, or spam an action;
* **ownership** -- a card may be bound to the OpenID it was issued for. Someone
  else clicking gets a "not your turn" refusal instead of hijacking the move;
* **flow** -- a button may declare ``next_card``, letting menus, questionnaires
  and loops be expressed without any code;
* **session** -- cards carry a ``session_id`` so a whole game's cards can be
  retired together when the match ends.

Concurrency note: the mutating helpers are executed under the store's lock by
the caller, so claim-then-act is atomic. That is what makes the one-shot check
a real guard rather than a racy hint.
"""
from __future__ import annotations

import copy
import hashlib
import re
import secrets
import time
from typing import Any

#: A game/flow can legitimately outlive a config card, but not forever.
DEFAULT_TTL_SECONDS = 3600
MAX_TTL_SECONDS = 86400

CARD_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,80}")

#: Who may click.
#:
#: ``everyone``   -- no restriction.
#: ``initiator``  -- whoever triggered this card. Resolved at *send* time, so a
#:                   card designed in the editor need not know the OpenID.
#: ``specified``  -- a literal OpenID, which must be non-empty.
OWNER_MODES = ("everyone", "initiator", "specified")

# Refusal codes mirror QQ's PUT /interactions contract so the client shows a
# sensible toast: 3 = duplicate/expired, 4 = no permission.
CODE_OK = 0
CODE_FAILED = 1
CODE_DUPLICATE = 3
CODE_FORBIDDEN = 4


class EphemeralError(Exception):
    """Raised with an ACK code so callers can answer QQ accurately."""

    def __init__(self, message: str, code: int = CODE_FAILED) -> None:
        super().__init__(message)
        self.code = code


def new_session_id() -> str:
    return secrets.token_urlsafe(12)


def new_nonce() -> str:
    return secrets.token_urlsafe(18)


def validate_card(value: object) -> dict[str, Any]:
    """Validate a code-generated ephemeral card.

    Deliberately strict: these cards come from other plugins, and a malformed
    card must fail here rather than at QQ.
    """
    if not isinstance(value, dict):
        raise EphemeralError("卡片必须是对象")
    card_id = str(value.get("id") or "").strip()
    if card_id and not CARD_ID_RE.fullmatch(card_id):
        raise EphemeralError("卡片 ID 只能包含字母、数字、点、下划线、冒号、横线")
    markdown = str(value.get("markdown") or "").strip()
    if not markdown or len(markdown) > 4000:
        raise EphemeralError("卡片 Markdown 必须为 1~4000 字符")

    rows = value.get("rows") or []
    if not isinstance(rows, list) or len(rows) > 5:
        raise EphemeralError("按钮最多 5 行")
    normalized: list[list[dict[str, Any]]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, list) or len(row) > 5:
            raise EphemeralError("每行最多 5 个按钮")
        built = []
        for button in row:
            item = _validate_button(button)
            if item["id"] in seen:
                raise EphemeralError(f"按钮 ID 重复: {item['id']}")
            seen.add(item["id"])
            built.append(item)
        normalized.append(built)

    ttl = int(value.get("ttl_seconds") or DEFAULT_TTL_SECONDS)
    if ttl <= 0 or ttl > MAX_TTL_SECONDS:
        raise EphemeralError(f"ttl_seconds 必须在 1~{MAX_TTL_SECONDS} 之间")

    owner_mode, owner_openid = _validate_owner(value, "卡片")
    return {
        "id": card_id or "ephemeral",
        "markdown": markdown,
        "rows": normalized,
        # Card-level one-shot: the whole card retires after any button click.
        "one_shot": bool(value.get("one_shot", False)),
        "owner_mode": owner_mode,
        # Only meaningful for owner_mode="specified"; "initiator" is filled in
        # at send time.
        "owner_openid": owner_openid,
        "owner_reject_tip": str(value.get("owner_reject_tip") or "").strip(),
        "ttl_seconds": ttl,
    }


def _validate_owner(value: dict, what: str) -> tuple[str, str]:
    """Normalise owner settings, rejecting the two ways they can be useless."""
    raw_openid = str(value.get("owner_openid") or "").strip()
    mode = str(value.get("owner_mode") or "").strip()
    if not mode:
        # Backwards compatible: a literal OpenID implies "specified".
        mode = "specified" if raw_openid else "everyone"
    if mode not in OWNER_MODES:
        raise EphemeralError(f"{what}归属模式无效")
    if mode == "specified" and not raw_openid:
        # A lock with an empty key matches nobody: the card would be dead.
        raise EphemeralError(f"{what}选择了「指定 OpenID」，OpenID 不能为空")
    if mode != "specified":
        raw_openid = ""
    return mode, raw_openid


def _validate_button(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EphemeralError("按钮必须是对象")
    label = str(value.get("label") or "").strip()
    if not label or len(label) > 64:
        raise EphemeralError("按钮文字必须为 1~64 字符")
    button_id = str(value.get("id") or "").strip()
    if button_id:
        if not CARD_ID_RE.fullmatch(button_id):
            raise EphemeralError("按钮 ID 含非法字符")
    else:
        # Derive one instead of demanding it. The old default was
        # ``button-<label>``, which is illegal for every Chinese label -- i.e.
        # for essentially every button this Hub renders -- so "omit the id"
        # was a documented option that always raised. Hash the label so the
        # id stays stable across re-renders of the same card, which is what
        # one-shot bookkeeping keys on.
        digest = hashlib.sha1(label.encode("utf-8")).hexdigest()[:12]
        button_id = f"button-{digest}"
    action_id = str(value.get("action_id") or "").strip()
    next_card = str(value.get("next_card") or "").strip()
    # An insert button is QQ's action.type=2: tapping appends text to the
    # input box and sends nothing. It needs no server round trip, so it must
    # not be forced to carry an action_id it would never invoke.
    insert_text = str(value.get("insert_text") or "")
    if insert_text and len(insert_text) > 100:
        raise EphemeralError("insert_text 最长 100 字符")
    if not action_id and not next_card and not insert_text:
        raise EphemeralError("按钮必须指定 action_id、next_card 或 insert_text")
    if action_id and not CARD_ID_RE.fullmatch(action_id):
        raise EphemeralError("action_id 含非法字符")
    if next_card and not CARD_ID_RE.fullmatch(next_card):
        raise EphemeralError("next_card 含非法字符")
    params = value.get("params") or {}
    if not isinstance(params, dict):
        raise EphemeralError("按钮 params 必须是 JSON 对象")
    style = value.get("style", 0)
    if style not in {0, 1}:
        raise EphemeralError("按钮样式只能是 0 或 1")
    _button_owner = _validate_owner(value, "按钮")
    return {
        "id": button_id,
        "label": label,
        "visited_label": str(value.get("visited_label") or label).strip()[:64],
        "style": style,
        "action_id": action_id,
        "next_card": next_card,
        "insert_text": insert_text,
        # type=2 only. Makes the message the user ends up sending a *quote* of
        # the card. For a picture board that is the difference between "a move
        # aimed at this position" and "someone typing 鼠 下 in conversation",
        # and it removes the need to match message ids by hand.
        "reply": bool(value.get("reply", False)),
        "params": params,
        # Button-level one-shot: only this button retires, card stays usable.
        "one_shot": bool(value.get("one_shot", False)),
        # Button-level ownership overrides the card's when not "everyone".
        "owner_mode": _button_owner[0],
        "owner_openid": _button_owner[1],
        "unsupport_tips": str(
            value.get("unsupport_tips") or "当前 QQ 版本不支持该按钮"
        ).strip()[:80],
    }


def bind_initiator(card: dict[str, Any], initiator_openid: str) -> dict[str, Any]:
    """Resolve ``owner_mode="initiator"`` into a concrete OpenID.

    Raises when the card wants an initiator but none exists -- which is exactly
    the proactive-push / scheduled / WebUI-test case. Silently downgrading to
    "everyone" would look locked while being open to the whole group, so this
    fails loudly instead.
    """
    card = copy.deepcopy(card)
    initiator = str(initiator_openid or "").strip()

    def resolve(node: dict[str, Any], what: str) -> None:
        if node.get("owner_mode") != "initiator":
            return
        if not initiator:
            raise EphemeralError(
                f"{what}设置为「仅发起者可用」，但本次发送没有发起者"
                "（主动推送/定时任务/后台测试发送）。请改为「所有人」或指定 OpenID。"
            )
        node["owner_openid"] = initiator

    resolve(card, "卡片")
    for row in card.get("rows", []):
        for button in row:
            resolve(button, f"按钮「{button.get('label', '')}」")
    return card


def build_record(origin: str, card: dict[str, Any], session_id: str) -> dict[str, Any]:
    now = int(time.time())
    return {
        "origin": origin,
        "session_id": session_id,
        "card": copy.deepcopy(card),
        "issued_at": now,
        "expires_at": now + int(card["ttl_seconds"]),
        "consumed": False,
        "used_buttons": [],
    }


def resolve_click(
    record: object,
    origin: str,
    button_id: str,
    member_openid: str,
    now: int | None = None,
) -> dict[str, Any]:
    """Validate a click against an issued ephemeral card.

    Raises :class:`EphemeralError` carrying the ACK code QQ should receive.
    Returns the matched button on success.
    """
    now = int(time.time()) if now is None else now
    if not isinstance(record, dict):
        raise EphemeralError("卡片不存在或已过期", CODE_DUPLICATE)
    if record.get("origin") != origin:
        # Cross-group replay of a leaked nonce.
        raise EphemeralError("卡片不属于本群", CODE_FORBIDDEN)
    if int(record.get("expires_at", 0)) <= now:
        raise EphemeralError("卡片已过期", CODE_DUPLICATE)
    if record.get("consumed"):
        raise EphemeralError("卡片已使用", CODE_DUPLICATE)

    card = record.get("card") or {}
    button = None
    for row in card.get("rows", []):
        for item in row:
            if item.get("id") == button_id:
                button = item
                break
        if button:
            break
    if button is None:
        raise EphemeralError("按钮不存在", CODE_FAILED)

    if button_id in (record.get("used_buttons") or []):
        raise EphemeralError("该按钮已使用", CODE_DUPLICATE)

    if button.get("owner_mode", "everyone") != "everyone":
        owner = str(button.get("owner_openid") or "")
    else:
        owner = str(card.get("owner_openid") or "") if card.get(
            "owner_mode", "everyone"
        ) != "everyone" else ""
    if owner and member_openid != owner:
        # Someone else's turn: refuse rather than acting on their behalf.
        raise EphemeralError(
            str(card.get("owner_reject_tip") or "这不是你的操作"), CODE_FORBIDDEN
        )
    return copy.deepcopy(button)


def apply_consumption(record: dict[str, Any], button: dict[str, Any]) -> None:
    """Mark the click as spent. Caller must hold the store lock."""
    card = record.get("card") or {}
    if card.get("one_shot"):
        record["consumed"] = True
    if button.get("one_shot"):
        used = list(record.get("used_buttons") or [])
        if button["id"] not in used:
            used.append(button["id"])
        record["used_buttons"] = used


def to_keyboard_rows(card: dict[str, Any], nonce: str) -> list[dict[str, Any]]:
    """Render to QQ keyboard payload.

    Most ephemeral buttons are type=1 callbacks: the click must reach the
    server so one-shot and ownership can be enforced, and ``button_data``
    stays opaque -- real parameters live in the server-side snapshot.

    A button carrying ``insert_text`` is emitted as **type=2** instead: QQ
    appends that text to the input box and sends nothing. That costs no
    round trip and no passive-reply budget, which is what makes it usable for
    things tapped many times in a row (an accidental, a note length) where a
    callback's latency and message cost would both be wrong.
    """
    rows = []
    for row in card.get("rows", []):
        buttons = []
        for item in row:
            insert_text = item.get("insert_text") or ""
            if insert_text:
                action = {
                    "type": 2,
                    "permission": {"type": 2},
                    "data": insert_text,
                    "enter": False,
                    # Quote the card the button lives on. A board card is a
                    # picture, so this makes every move an explicit reply to
                    # the position it was played against.
                    "reply": bool(item.get("reply", False)),
                    "unsupport_tips": item["unsupport_tips"],
                }
            else:
                action = {
                    "type": 1,
                    "permission": {"type": 2},
                    "data": f"qqhub:e1:{nonce}:{item['id']}",
                    "unsupport_tips": item["unsupport_tips"],
                }
            buttons.append({
                "id": item["id"],
                "render_data": {
                    "label": item["label"],
                    "visited_label": item["visited_label"],
                    "style": item["style"],
                },
                "action": action,
            })
        rows.append({"buttons": buttons})
    return rows
