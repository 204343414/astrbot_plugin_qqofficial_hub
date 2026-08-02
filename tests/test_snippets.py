"""Snippet catalog: opt-in templates for the card editor."""
from qqofficial_hub.snippets import SNIPPETS, catalog
from qqofficial_hub.store import _validate_markdown, empty_panel


def test_catalog_is_serialisable_and_unique():
    items = catalog()
    assert items and all(isinstance(i, dict) for i in items)
    ids = [i["id"] for i in items]
    assert len(ids) == len(set(ids))
    for item in items:
        assert item["label"] and item["snippet"] and item["group"]


def test_default_panel_has_no_dynamic_token():
    """Dynamic tokens must be opt-in, never forced on every card."""
    assert "{{" not in empty_panel()["markdown"]


def test_every_snippet_passes_markdown_validation():
    for item in SNIPPETS:
        _validate_markdown(f"# t\n{item.snippet}")


def test_dynamic_snippets_are_flagged():
    for item in SNIPPETS:
        assert item.dynamic == ("{{" in item.snippet), item.id


def test_no_push_status_tokens_remain():
    """The proactive-push lamp was removed: QQ cannot report the setting."""
    for item in SNIPPETS:
        assert "push" not in item.id
        assert "push" not in item.snippet


def test_static_snippets_have_no_placeholder():
    for item in SNIPPETS:
        if not item.dynamic:
            assert "{{" not in item.snippet
