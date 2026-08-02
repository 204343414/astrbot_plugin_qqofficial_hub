"""Editor panel -> ephemeral card conversion."""
import pytest

from qqofficial_hub.ephemeral import EphemeralError, validate_card
from qqofficial_hub.panel_convert import panel_to_ephemeral
from qqofficial_hub.store import empty_panel, validate_panel


def test_switches_survive_the_round_trip():
    panel = validate_panel({**empty_panel(), "one_shot": True,
                            "owner_mode": "specified", "owner_openid": "A1",
                            "owner_reject_tip": "轮到对手"})
    card = validate_card(panel_to_ephemeral(panel))
    assert card["one_shot"] is True
    assert card["owner_mode"] == "specified"
    assert card["owner_openid"] == "A1"
    assert card["owner_reject_tip"] == "轮到对手"


def test_button_switches_survive():
    panel = empty_panel()
    panel["rows"][0][0].update({"one_shot": True, "owner_mode": "specified",
                                "owner_openid": "B2"})
    card = validate_card(panel_to_ephemeral(validate_panel(panel)))
    button = card["rows"][0][0]
    assert button["one_shot"] is True
    assert button["owner_openid"] == "B2"


def test_non_callback_buttons_are_dropped():
    """URL/command buttons cannot round-trip to the server, so one-shot and
    ownership could not be enforced on them."""
    card = panel_to_ephemeral(validate_panel(empty_panel()))
    kept = [b["id"] for row in card["rows"] for b in row]
    assert "docs" not in kept, "URL button must be dropped"
    assert "insert" not in kept, "command-input button must be dropped"
    assert "refresh" in kept


def test_params_are_carried_over():
    panel = empty_panel()
    panel["rows"][0][0]["action_params"] = {"cell": 4}
    card = panel_to_ephemeral(validate_panel(panel))
    assert card["rows"][0][0]["params"] == {"cell": 4}


def test_converted_card_always_validates():
    assert validate_card(panel_to_ephemeral(validate_panel(empty_panel())))


def test_empty_openid_still_rejected_after_conversion():
    panel = validate_panel(empty_panel())
    panel["owner_mode"] = "specified"
    panel["owner_openid"] = ""
    with pytest.raises(EphemeralError, match="不能为空"):
        validate_card(panel_to_ephemeral(panel))
