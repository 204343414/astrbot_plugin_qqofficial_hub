"""Detect whether a QQ group has proactive push enabled.

Why a dedicated probe
---------------------
Reading the state from authorize events alone is not enough: QQ only emits
``INTERACTION_CREATE`` type 18/19 when authorization *changes*. Groups that
were authorized before install, or never authorized, stay silent forever.

The reliable everyday signal is the outcome of a real proactive send. This
module centralises that inference so every code path — Hub's own panel, the
synthetic-command fallback, and other plugins reporting in — funnels into one
place instead of each guessing separately.

Evidence ranking (highest wins on ties):

``authorize`` > ``send`` > ``adapter``

An authorize event is QQ's own statement; a send result is empirical; an
adapter-level refusal (AstrBot skipping the send) is the weakest but still
decisive when it says "no".
"""
from __future__ import annotations

import re

GRANTED = "granted"
REVOKED = "revoked"
UNKNOWN = "unknown"

SOURCE_RANK = {"adapter": 1, "send": 2, "authorize": 3}

#: QQ error fragments that mean "this group has not enabled proactive push".
#: Matched case-insensitively against the stringified exception.
_REVOKED_PATTERNS = (
    "主动消息",
    "主动推送",
    "主动发言",
    "no permission to send proactive",
    "push message is not allowed",
    "11244",   # 机器人未开启主动消息
    "11253",
    "304003",
    "40054",
)

#: Fragments that indicate an unrelated failure (rate limit, audit, bad param).
#: These must NOT be read as "push disabled".
_UNRELATED_PATTERNS = (
    "event_id",
    "msg_id",
    "audit",
    "审核",
    "频率",
    "frequency",
    "rate limit",
    "too many",
    "限频",
    "invalid parameter",
    "参数",
)


def classify_send_error(error: object) -> str:
    """Map a failed *proactive* send to a push state.

    Returns ``REVOKED`` only when the failure clearly indicates missing
    proactive-push capability; otherwise ``UNKNOWN`` so an unrelated error
    (audit, rate limit, malformed payload) never mislabels a healthy group.
    """
    text = str(error or "").lower()
    if not text:
        return UNKNOWN
    if any(token.lower() in text for token in _UNRELATED_PATTERNS):
        return UNKNOWN
    if any(token.lower() in text for token in _REVOKED_PATTERNS):
        return REVOKED
    return UNKNOWN


def should_replace(old_state: str, old_source: str, new_state: str, new_source: str) -> bool:
    """Decide whether a newly observed state supersedes the stored one."""
    if new_state == UNKNOWN:
        return False
    if old_state == UNKNOWN:
        return True
    new_rank = SOURCE_RANK.get(new_source, 0)
    old_rank = SOURCE_RANK.get(old_source, 0)
    if new_rank != old_rank:
        return new_rank > old_rank
    # Same authority: the newer observation wins, including flips.
    return True


_ORIGIN_RE = re.compile(r"^[^:]+:GroupMessage:(.+)$")


def group_openid_of(origin: str) -> str:
    match = _ORIGIN_RE.match(str(origin or ""))
    return match.group(1) if match else ""
