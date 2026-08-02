"""Hub must only ever fire on QQ Official adapters.

Motivation: the user runs a NapCat account (heavy/risky plugins + LLM) next to
a QQ Official account (safe plugins only). A handler leaking across accounts is
exactly the kind of mistake that got a previous bot banned.
"""
import sys
import types
from types import SimpleNamespace


def _load_filter_module():
    """Load AstrBot's real filter, or a faithful stand-in when absent."""
    try:
        from astrbot.core.star.filter.platform_adapter_type import (  # noqa: F401
            ADAPTER_NAME_2_TYPE, PlatformAdapterType, PlatformAdapterTypeFilter,
        )
        return ADAPTER_NAME_2_TYPE, PlatformAdapterType, PlatformAdapterTypeFilter
    except Exception:
        import enum

        class PlatformAdapterType(enum.Flag):
            AIOCQHTTP = enum.auto()
            QQOFFICIAL = enum.auto()
            QQOFFICIAL_WEBHOOK = enum.auto()
            TELEGRAM = enum.auto()
            ALL = enum.auto()

        ADAPTER_NAME_2_TYPE = {
            "aiocqhttp": PlatformAdapterType.AIOCQHTTP,
            "qq_official": PlatformAdapterType.QQOFFICIAL,
            "qq_official_webhook": PlatformAdapterType.QQOFFICIAL_WEBHOOK,
            "telegram": PlatformAdapterType.TELEGRAM,
        }

        class PlatformAdapterTypeFilter:
            def __init__(self, t):
                self.platform_type = t

            def filter(self, event, cfg=None):
                if self.platform_type & PlatformAdapterType.ALL:
                    return True
                name = event.get_platform_name()
                if name in ADAPTER_NAME_2_TYPE:
                    return bool(ADAPTER_NAME_2_TYPE[name] & self.platform_type)
                return False

        return ADAPTER_NAME_2_TYPE, PlatformAdapterType, PlatformAdapterTypeFilter


_, PlatformAdapterType, PlatformAdapterTypeFilter = _load_filter_module()

GATE = PlatformAdapterType.QQOFFICIAL | PlatformAdapterType.QQOFFICIAL_WEBHOOK


def _event(name):
    return SimpleNamespace(get_platform_name=lambda: name)


def test_gate_allows_both_official_adapters():
    f = PlatformAdapterTypeFilter(GATE)
    assert f.filter(_event("qq_official"), None)
    assert f.filter(_event("qq_official_webhook"), None)


def test_gate_blocks_napcat_and_others():
    """aiocqhttp is NapCat: the account that must NOT get Hub handlers."""
    f = PlatformAdapterTypeFilter(GATE)
    assert not f.filter(_event("aiocqhttp"), None)
    assert not f.filter(_event("telegram"), None)
    assert not f.filter(_event("unknown_platform"), None)


def test_every_handler_declares_the_gate():
    """Any new @filter handler must carry the platform gate too."""
    from pathlib import Path
    source = Path(__file__).parents[1].joinpath("main.py").read_text(encoding="utf-8")
    lines = source.splitlines()
    entry_points = [
        i for i, line in enumerate(lines)
        if line.strip().startswith(("@filter.command(", "@filter.event_message_type("))
    ]
    assert entry_points, "expected decorated handlers"
    for index in entry_points:
        window = "\n".join(lines[max(0, index - 5):index])
        assert "platform_adapter_type" in window, (
            f"line {index + 1} lacks a platform gate: {lines[index].strip()}"
        )
