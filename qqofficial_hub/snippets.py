"""Insertable Markdown snippets offered by the card editor.

Design intent
-------------
Dynamic tokens are **opt-in**. Nothing here is forced into a panel: the editor
lists these as insertable templates and the card author decides what a given
card needs. A blueprint/board-game card that never cares about proactive push
simply never inserts the push tokens.

Adding a new dynamic token means:

1. append a :class:`Snippet` here (so the editor lists it automatically);
2. resolve its placeholder at send time (see ``main._render_dynamic_markdown``).

Only add a dynamic token when its value can actually be known at send time.
A previous proactive-push lamp was removed precisely because QQ offers no way
to query the current setting, leaving it permanently "unknown".

Keeping the catalog server-side means new blueprint tokens show up in the UI
without shipping new front-end code.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class Snippet:
    id: str
    label: str
    #: Text inserted at the cursor.
    snippet: str
    #: Short explanation shown under the button.
    hint: str
    #: Grouping key for the editor UI.
    group: str
    #: True when the snippet contains a placeholder resolved at send time.
    dynamic: bool = False


SNIPPETS: tuple[Snippet, ...] = (
    Snippet(
        id="group_name",
        label="当前群标识",
        snippet="{{group_openid_short}}",
        hint="发送时替换为本群 openid 后 8 位，便于多群蓝图区分。",
        group="状态占位符",
        dynamic=True,
    ),
    Snippet(
        id="heading",
        label="标题",
        snippet="# 标题\n",
        hint="一级标题。",
        group="排版",
    ),
    Snippet(
        id="bold",
        label="粗体",
        snippet="**粗体**",
        hint="加粗文字。",
        group="排版",
    ),
    Snippet(
        id="quote",
        label="引用",
        snippet="> 引用内容\n",
        hint="引用块，适合做提示语。",
        group="排版",
    ),
    Snippet(
        id="divider",
        label="分隔线",
        snippet="\n---\n",
        hint="水平分隔线，用于分区。",
        group="排版",
    ),
    Snippet(
        id="list",
        label="列表",
        snippet="- 条目一\n- 条目二\n",
        hint="无序列表。",
        group="排版",
    ),
)


def catalog() -> list[dict]:
    """Serialisable snippet catalog for the editor bootstrap payload."""
    return [asdict(item) for item in SNIPPETS]
