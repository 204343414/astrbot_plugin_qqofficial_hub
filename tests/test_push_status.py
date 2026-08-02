"""Push-status lamp must never lie about a group's state."""
from types import SimpleNamespace

from qqofficial_hub import push_status as ps


def test_no_signal_renders_unknown_not_disabled():
    """Groups authorized before install emit no event; must not claim 未开启."""
    out = ps.render("{{push_lamp}} {{push_status}}", ps.UNKNOWN)
    assert out == "⚪ 当前群主动消息推送状态未知"
    assert "未开启" not in out


def test_wording_avoids_the_word_permission():
    for text in ps.DEFAULT_TEMPLATES.values():
        assert "权限" not in text, "avoid 权限, it reads as accusatory"


def test_render_all_states():
    assert ps.render("{{push_lamp}}", ps.GRANTED) == "🟢"
    assert ps.render("{{push_status}}", ps.REVOKED) == "当前群未开启主动消息推送功能"


def test_custom_lamp_and_template_override():
    out = ps.render(
        "{{push_lamp}}|{{push_status}}", ps.REVOKED,
        lamps={ps.REVOKED: "⛔"}, templates={ps.REVOKED: "自定义文案"},
    )
    assert out == "⛔|自定义文案"


def test_blank_override_falls_back_to_default():
    out = ps.render("{{push_lamp}}", ps.GRANTED, lamps={ps.GRANTED: ""})
    assert out == "🟢"


def test_unknown_state_string_degrades_instead_of_raising():
    assert ps.render("{{push_lamp}}", "garbage") == "⚪"


def test_has_placeholder():
    assert ps.has_placeholder("a {{push_lamp}} b")
    assert ps.has_placeholder("a {{push_status}} b")
    assert not ps.has_placeholder("plain card")


def test_markdown_without_placeholder_is_untouched():
    md = "# 标题\n[🔗链接](https://bot.q.qq.com/)"
    assert ps.render(md, ps.REVOKED) == md


def test_authorize_event_mapping():
    assert ps.state_from_authorize_event("group_push", None) == ps.GRANTED
    assert ps.state_from_authorize_event("group_push", False) == ps.REVOKED
    assert ps.state_from_authorize_event("group_push", "cancel") == ps.REVOKED
    # c2c scope is not a group signal
    assert ps.state_from_authorize_event("c2c_push", None) is None
    assert ps.state_from_authorize_event("c2c_push", None, is_group=False) == ps.GRANTED
    assert ps.state_from_authorize_event("", None) is None


def test_placeholders_survive_panel_markdown_validation():
    from qqofficial_hub.store import _validate_markdown
    _validate_markdown("# t\n{{push_lamp}} {{push_status}}")
