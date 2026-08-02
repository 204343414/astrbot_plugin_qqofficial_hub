"""Ephemeral cards: one-shot and ownership must be real guards, not hints."""
import asyncio
import tempfile
from pathlib import Path

import pytest

from qqofficial_hub import ephemeral as ep
from qqofficial_hub.store import PanelStore

ORIGIN = "qq_official:GroupMessage:G1"
ALICE = "OPENID_ALICE"
BOB = "OPENID_BOB"


def _card(**over):
    card = {
        "id": "board",
        "markdown": "# 井字棋",
        "rows": [[{"id": "c0", "label": "1", "action_id": "ttt.move",
                   "params": {"cell": 0}}]],
    }
    card.update(over)
    return ep.validate_card(card)


def _store():
    store = PanelStore(Path(tempfile.mkdtemp()))
    asyncio.get_event_loop()
    return store


# --- validation -------------------------------------------------------------

def test_button_requires_action_or_next_card():
    with pytest.raises(ep.EphemeralError):
        ep.validate_card({"markdown": "x", "rows": [[{"id": "a", "label": "A"}]]})


def test_rejects_duplicate_button_ids():
    with pytest.raises(ep.EphemeralError, match="重复"):
        ep.validate_card({"markdown": "x", "rows": [[
            {"id": "a", "label": "A", "action_id": "x"},
            {"id": "a", "label": "B", "action_id": "x"},
        ]]})


def test_enforces_qq_5x5_limits():
    row = [{"id": f"b{i}", "label": "x", "action_id": "a"} for i in range(6)]
    with pytest.raises(ep.EphemeralError, match="每行"):
        ep.validate_card({"markdown": "x", "rows": [row]})
    rows = [[{"id": f"r{i}", "label": "x", "action_id": "a"}] for i in range(6)]
    with pytest.raises(ep.EphemeralError, match="5 行"):
        ep.validate_card({"markdown": "x", "rows": rows})


def test_ttl_is_bounded():
    with pytest.raises(ep.EphemeralError):
        ep.validate_card({"markdown": "x", "ttl_seconds": ep.MAX_TTL_SECONDS + 1,
                          "rows": [[{"id": "a", "label": "A", "action_id": "x"}]]})


def test_keyboard_rows_are_all_callback_buttons():
    rows = ep.to_keyboard_rows(_card(), "NONCE")
    button = rows[0]["buttons"][0]
    assert button["action"]["type"] == 1, "must round-trip to the server"
    assert button["action"]["data"] == "qqhub:e1:NONCE:c0"
    # real params stay server-side
    assert "cell" not in button["action"]["data"]


# --- one-shot ---------------------------------------------------------------

def test_card_level_one_shot_retires_whole_card():
    async def scenario():
        store = _store()
        await store.bootstrap()
        nonce, _ = await store.issue_ephemeral_card(ORIGIN, _card(one_shot=True))
        await store.claim_ephemeral_click(ORIGIN, nonce, "c0", ALICE)
        with pytest.raises(ep.EphemeralError) as err:
            await store.claim_ephemeral_click(ORIGIN, nonce, "c0", ALICE)
        assert err.value.code == ep.CODE_DUPLICATE
    asyncio.run(scenario())


def test_button_level_one_shot_keeps_card_alive():
    async def scenario():
        store = _store()
        await store.bootstrap()
        card = ep.validate_card({"markdown": "x", "rows": [[
            {"id": "a", "label": "A", "action_id": "act", "one_shot": True},
            {"id": "b", "label": "B", "action_id": "act"},
        ]]})
        nonce, _ = await store.issue_ephemeral_card(ORIGIN, card)
        await store.claim_ephemeral_click(ORIGIN, nonce, "a", ALICE)
        with pytest.raises(ep.EphemeralError):
            await store.claim_ephemeral_click(ORIGIN, nonce, "a", ALICE)
        # sibling button still works
        button, _ = await store.claim_ephemeral_click(ORIGIN, nonce, "b", ALICE)
        assert button["id"] == "b"
    asyncio.run(scenario())


def test_non_one_shot_button_is_repeatable():
    async def scenario():
        store = _store()
        await store.bootstrap()
        nonce, _ = await store.issue_ephemeral_card(ORIGIN, _card())
        for _ in range(3):
            await store.claim_ephemeral_click(ORIGIN, nonce, "c0", ALICE)
    asyncio.run(scenario())


def test_concurrent_clicks_cannot_both_win():
    """The lock must make claim-then-consume atomic."""
    async def scenario():
        store = _store()
        await store.bootstrap()
        nonce, _ = await store.issue_ephemeral_card(ORIGIN, _card(one_shot=True))
        results = await asyncio.gather(
            store.claim_ephemeral_click(ORIGIN, nonce, "c0", ALICE),
            store.claim_ephemeral_click(ORIGIN, nonce, "c0", ALICE),
            return_exceptions=True,
        )
        ok = [r for r in results if not isinstance(r, Exception)]
        failed = [r for r in results if isinstance(r, ep.EphemeralError)]
        assert len(ok) == 1 and len(failed) == 1
    asyncio.run(scenario())


# --- ownership --------------------------------------------------------------

def test_other_player_cannot_click_owned_card():
    async def scenario():
        store = _store()
        await store.bootstrap()
        nonce, _ = await store.issue_ephemeral_card(
            ORIGIN, _card(owner_openid=ALICE, owner_reject_tip="现在轮到对手")
        )
        with pytest.raises(ep.EphemeralError) as err:
            await store.claim_ephemeral_click(ORIGIN, nonce, "c0", BOB)
        assert err.value.code == ep.CODE_FORBIDDEN
        assert "轮到" in str(err.value)
        # the rightful owner is unaffected
        button, _ = await store.claim_ephemeral_click(ORIGIN, nonce, "c0", ALICE)
        assert button["id"] == "c0"
    asyncio.run(scenario())


def test_button_owner_overrides_card_owner():
    async def scenario():
        store = _store()
        await store.bootstrap()
        card = ep.validate_card({"markdown": "x", "owner_openid": ALICE, "rows": [[
            {"id": "mine", "label": "A", "action_id": "act"},
            {"id": "theirs", "label": "B", "action_id": "act", "owner_openid": BOB},
        ]]})
        nonce, _ = await store.issue_ephemeral_card(ORIGIN, card)
        button, _ = await store.claim_ephemeral_click(ORIGIN, nonce, "theirs", BOB)
        assert button["id"] == "theirs"
        with pytest.raises(ep.EphemeralError):
            await store.claim_ephemeral_click(ORIGIN, nonce, "mine", BOB)
    asyncio.run(scenario())


def test_unowned_card_is_open_to_everyone():
    async def scenario():
        store = _store()
        await store.bootstrap()
        nonce, _ = await store.issue_ephemeral_card(ORIGIN, _card())
        await store.claim_ephemeral_click(ORIGIN, nonce, "c0", BOB)
    asyncio.run(scenario())


# --- isolation and lifecycle -----------------------------------------------

def test_card_cannot_be_replayed_in_another_group():
    async def scenario():
        store = _store()
        await store.bootstrap()
        nonce, _ = await store.issue_ephemeral_card(ORIGIN, _card())
        with pytest.raises(ep.EphemeralError) as err:
            await store.claim_ephemeral_click(
                "qq_official:GroupMessage:OTHER", nonce, "c0", ALICE
            )
        assert err.value.code == ep.CODE_FORBIDDEN
    asyncio.run(scenario())


def test_expired_card_is_refused():
    record = ep.build_record(ORIGIN, _card(), "s1")
    record["expires_at"] = 100
    with pytest.raises(ep.EphemeralError) as err:
        ep.resolve_click(record, ORIGIN, "c0", ALICE, now=101)
    assert err.value.code == ep.CODE_DUPLICATE


def test_ending_a_session_retires_all_its_cards():
    async def scenario():
        store = _store()
        await store.bootstrap()
        _, session = await store.issue_ephemeral_card(ORIGIN, _card())
        nonce2, _ = await store.issue_ephemeral_card(ORIGIN, _card(), session)
        other, _ = await store.issue_ephemeral_card(ORIGIN, _card())
        assert await store.end_ephemeral_session(session) == 2
        with pytest.raises(ep.EphemeralError):
            await store.claim_ephemeral_click(ORIGIN, nonce2, "c0", ALICE)
        # unrelated session survives
        await store.claim_ephemeral_click(ORIGIN, other, "c0", ALICE)
    asyncio.run(scenario())


def test_ephemeral_cards_do_not_touch_group_config():
    """Games must never pollute the configured panel or bump its revision."""
    async def scenario():
        store = _store()
        snapshot = await store.bootstrap()
        before = snapshot["templates"]["default_panel"]["revision"]
        await store.issue_ephemeral_card(ORIGIN, _card())
        after = (await store.bootstrap())["templates"]["default_panel"]["revision"]
        assert before == after
        assert ORIGIN not in (await store.bootstrap())["group_overrides"]
    asyncio.run(scenario())


# --- owner modes: the "what is invalid" question -----------------------------

def test_specified_mode_rejects_empty_openid():
    """A lock with an empty key matches nobody -- the card would be dead."""
    with pytest.raises(ep.EphemeralError, match="不能为空"):
        ep.validate_card({"markdown": "x", "owner_mode": "specified",
                          "rows": [[{"id": "a", "label": "A", "action_id": "x"}]]})


def test_button_specified_mode_rejects_empty_openid():
    with pytest.raises(ep.EphemeralError, match="不能为空"):
        ep.validate_card({"markdown": "x", "rows": [[
            {"id": "a", "label": "A", "action_id": "x", "owner_mode": "specified"}
        ]]})


def test_initiator_mode_fails_loudly_without_an_initiator():
    """Proactive push / scheduled / WebUI test send has no initiator.

    Downgrading to "everyone" would look locked while being wide open, so the
    send must fail instead.
    """
    card = ep.validate_card({"markdown": "x", "owner_mode": "initiator",
                             "rows": [[{"id": "a", "label": "A", "action_id": "x"}]]})
    with pytest.raises(ep.EphemeralError, match="没有发起者"):
        ep.bind_initiator(card, "")


def test_initiator_mode_binds_the_clicker():
    card = ep.validate_card({"markdown": "x", "owner_mode": "initiator",
                             "rows": [[{"id": "a", "label": "A", "action_id": "x"}]]})
    bound = ep.bind_initiator(card, ALICE)
    assert bound["owner_openid"] == ALICE
    record = ep.build_record(ORIGIN, bound, "s1")
    with pytest.raises(ep.EphemeralError) as err:
        ep.resolve_click(record, ORIGIN, "a", BOB)
    assert err.value.code == ep.CODE_FORBIDDEN
    assert ep.resolve_click(record, ORIGIN, "a", ALICE)["id"] == "a"


def test_button_initiator_binds_independently():
    card = ep.validate_card({"markdown": "x", "rows": [[
        {"id": "mine", "label": "A", "action_id": "x", "owner_mode": "initiator"},
        {"id": "open", "label": "B", "action_id": "x"},
    ]]})
    bound = ep.bind_initiator(card, ALICE)
    record = ep.build_record(ORIGIN, bound, "s1")
    with pytest.raises(ep.EphemeralError):
        ep.resolve_click(record, ORIGIN, "mine", BOB)
    # the unrestricted sibling stays open to everyone
    assert ep.resolve_click(record, ORIGIN, "open", BOB)["id"] == "open"


def test_everyone_mode_ignores_a_stray_openid():
    card = ep.validate_card({"markdown": "x", "owner_mode": "everyone",
                             "owner_openid": "LEFTOVER",
                             "rows": [[{"id": "a", "label": "A", "action_id": "x"}]]})
    assert card["owner_openid"] == "", "stale value must not silently lock the card"
    record = ep.build_record(ORIGIN, card, "s1")
    assert ep.resolve_click(record, ORIGIN, "a", BOB)["id"] == "a"


def test_bare_openid_still_implies_specified_mode():
    """Backwards compatibility with cards written before owner_mode existed."""
    card = ep.validate_card({"markdown": "x", "owner_openid": ALICE,
                             "rows": [[{"id": "a", "label": "A", "action_id": "x"}]]})
    assert card["owner_mode"] == "specified"
    record = ep.build_record(ORIGIN, card, "s1")
    with pytest.raises(ep.EphemeralError):
        ep.resolve_click(record, ORIGIN, "a", BOB)


def test_bind_initiator_is_noop_without_initiator_mode():
    card = ep.validate_card({"markdown": "x",
                             "rows": [[{"id": "a", "label": "A", "action_id": "x"}]]})
    assert ep.bind_initiator(card, "") == card
