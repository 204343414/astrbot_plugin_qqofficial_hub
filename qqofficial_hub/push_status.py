"""Proactive-push status for a QQ Official group, and its card placeholders.

Why three states
----------------
QQ only tells us about proactive-push authorization when it *changes*
(``INTERACTION_CREATE`` type 18/19/20 carrying ``authorize_data.scope``).
A group authorized before this plugin was installed, or never authorized at
all, produces no event whatsoever.

Therefore "no record" must render as **unknown**, not as "not enabled" —
claiming the latter would be a lie the user can see.

A second, empirical signal is far more reliable in practice: if a proactive
send actually succeeds, push is on; if QQ rejects it, push is off. Both feed
the same store.

Wording note: the visible strings deliberately avoid the word 权限, which reads
as accusatory. We describe a feature that is not switched on.
"""
from __future__ import annotations

GRANTED = "granted"
REVOKED = "revoked"
UNKNOWN = "unknown"

#: Placeholders usable inside a panel's markdown.
LAMP_TOKEN = "{{push_lamp}}"
STATUS_TOKEN = "{{push_status}}"

DEFAULT_LAMPS = {
    GRANTED: "🟢",
    REVOKED: "🔴",
    UNKNOWN: "⚪",
}

DEFAULT_TEMPLATES = {
    GRANTED: "当前群已开启主动消息推送功能",
    REVOKED: "当前群未开启主动消息推送功能",
    UNKNOWN: "当前群主动消息推送状态未知",
}

#: QQ scopes that mean "proactive push" for a group / single chat.
GROUP_PUSH_SCOPE = "group_push"
C2C_PUSH_SCOPE = "c2c_push"


def normalize_state(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in (GRANTED, REVOKED, UNKNOWN) else UNKNOWN


def render(markdown: str, state: str, *, lamps: dict | None = None,
           templates: dict | None = None) -> str:
    """Substitute push-status placeholders in a panel's markdown.

    Unknown/typo'd states degrade to UNKNOWN rather than raising: a card that
    renders slightly vaguely beats a card that fails to send.
    """
    state = normalize_state(state)
    lamp = (lamps or {}).get(state) or DEFAULT_LAMPS[state]
    text = (templates or {}).get(state) or DEFAULT_TEMPLATES[state]
    return markdown.replace(LAMP_TOKEN, lamp).replace(STATUS_TOKEN, text)


def has_placeholder(markdown: str) -> bool:
    return LAMP_TOKEN in markdown or STATUS_TOKEN in markdown


def state_from_authorize_event(scope: str, authorized: object,
                               *, is_group: bool = True) -> str | None:
    """Map an authorize event to a push state, or None if unrelated.

    ``authorized`` is intentionally loose: the documented payload lists
    ``opt_scene``/``scope`` but not an explicit boolean, and real events may
    carry one under several names. When no boolean is present, receiving the
    event at all is treated as a grant, which matches the documented
    "用户授权/群授权" semantics.
    """
    wanted = GROUP_PUSH_SCOPE if is_group else C2C_PUSH_SCOPE
    if str(scope or "").strip() != wanted:
        return None
    if authorized is None:
        return GRANTED
    if isinstance(authorized, str):
        lowered = authorized.strip().lower()
        if lowered in ("0", "false", "off", "no", "deny", "denied", "cancel"):
            return REVOKED
        return GRANTED
    return GRANTED if bool(authorized) else REVOKED
