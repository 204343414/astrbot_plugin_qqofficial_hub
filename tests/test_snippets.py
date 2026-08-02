"""Snippet catalog: opt-in templates for the card editor."""
from qqofficial_hub import push_status as ps
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


def test_dynamic_snippets_are_flagged_and_resolvable():
    dynamic = [i for i in SNIPPETS if i.dynamic]
    assert dynamic, "expected at least the push-status tokens"
    for item in dynamic:
        assert "{{" in item.snippet
    # every push token the catalog offers must be understood by the renderer
    for item in dynamic:
        if "push" in item.id:
            rendered = ps.render(item.snippet, ps.REVOKED)
            assert "{{" not in rendered, f"{item.id} left an unresolved token"


def test_static_snippets_have_no_placeholder():
    for item in SNIPPETS:
        if not item.dynamic:
            assert "{{" not in item.snippet
