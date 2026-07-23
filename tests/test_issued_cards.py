from datetime import datetime, timedelta, timezone

from qqofficial_hub.issued_cards import IssuedCard, ValidationCode


def card() -> IssuedCard:
    now = datetime(2026, 7, 23, tzinfo=timezone.utc)
    return IssuedCard(
        nonce="opaque-nonce",
        platform_instance_id="头条flag",
        group_openid="GROUP_OPENID",
        panel_id="default_panel",
        panel_revision=7,
        button_ids=frozenset({"refresh", "status"}),
        issued_at=now,
        expires_at=now + timedelta(hours=24),
    )


def test_callback_must_match_issued_group_platform_revision_and_button():
    now = datetime(2026, 7, 23, 1, tzinfo=timezone.utc)
    issued = card()
    assert issued.validate(
        platform_instance_id="头条flag",
        group_openid="GROUP_OPENID",
        button_id="refresh",
        current_panel_revision=7,
        now=now,
    ) is ValidationCode.OK
    assert issued.validate(
        platform_instance_id="other",
        group_openid="GROUP_OPENID",
        button_id="refresh",
        current_panel_revision=7,
        now=now,
    ) is ValidationCode.WRONG_PLATFORM
    assert issued.validate(
        platform_instance_id="头条flag",
        group_openid="other-group",
        button_id="refresh",
        current_panel_revision=7,
        now=now,
    ) is ValidationCode.WRONG_GROUP
    assert issued.validate(
        platform_instance_id="头条flag",
        group_openid="GROUP_OPENID",
        button_id="refresh",
        current_panel_revision=8,
        now=now,
    ) is ValidationCode.STALE_REVISION
    assert issued.validate(
        platform_instance_id="头条flag",
        group_openid="GROUP_OPENID",
        button_id="not-issued",
        current_panel_revision=7,
        now=now,
    ) is ValidationCode.WRONG_BUTTON


def test_expired_card_is_rejected():
    issued = card()
    assert issued.validate(
        platform_instance_id="头条flag",
        group_openid="GROUP_OPENID",
        button_id="refresh",
        current_panel_revision=7,
        now=issued.expires_at,
    ) is ValidationCode.EXPIRED
