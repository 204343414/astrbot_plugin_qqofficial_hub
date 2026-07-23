"""Validation primitives for issued QQ Official callback cards.

This module intentionally has no AstrBot or botpy import.  It defines the
server-side facts required before an Interaction callback may run a Hub action.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class ValidationCode(str, Enum):
    OK = "ok"
    UNKNOWN_CARD = "unknown_card"
    EXPIRED = "expired"
    WRONG_PLATFORM = "wrong_platform"
    WRONG_GROUP = "wrong_group"
    STALE_REVISION = "stale_revision"
    WRONG_BUTTON = "wrong_button"


@dataclass(frozen=True, slots=True)
class IssuedCard:
    """A persisted callback-card capability, scoped to exactly one QQ group.

    ``nonce`` is random opaque server state. It is not a timestamp and must
    never encode arbitrary command text or executable data.
    """

    nonce: str
    platform_instance_id: str
    group_openid: str
    panel_id: str
    panel_revision: int
    button_ids: frozenset[str]
    issued_at: datetime
    expires_at: datetime

    def validate(
        self,
        *,
        platform_instance_id: str,
        group_openid: str,
        button_id: str,
        current_panel_revision: int,
        now: datetime | None = None,
    ) -> ValidationCode:
        """Validate immutable callback scope before permission/action checks."""
        if now is None:
            now = datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        if self.expires_at.tzinfo is None:
            raise ValueError("expires_at must be timezone-aware")
        if now >= self.expires_at:
            return ValidationCode.EXPIRED
        if platform_instance_id != self.platform_instance_id:
            return ValidationCode.WRONG_PLATFORM
        if group_openid != self.group_openid:
            return ValidationCode.WRONG_GROUP
        if current_panel_revision != self.panel_revision:
            return ValidationCode.STALE_REVISION
        if button_id not in self.button_ids:
            return ValidationCode.WRONG_BUTTON
        return ValidationCode.OK
