"""Process-local, owner-scoped Action Registry for QQ type=1 callbacks."""
from __future__ import annotations

import asyncio
import builtins
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

_REGISTRY_KEY = "_ASTRBOT_QQHUB_ACTION_REGISTRY_V1"
ActionCallback = Callable[["ActionContext", dict[str, Any]], Awaitable[int]]

#: Bumped only when the registry's *method surface* changes incompatibly.
#: See ``get_action_registry`` for why an ``isinstance`` check is not usable.
REGISTRY_PROTOCOL = 1
_REGISTRY_SURFACE = (
    "register", "unregister_owner", "catalog", "contains", "execute",
)


@dataclass(frozen=True, slots=True)
class ActionContext:
    client: Any
    interaction: Any
    origin: str
    group_openid: str
    member_openid: str
    mention_clicker: bool = False


@dataclass(frozen=True, slots=True)
class EphemeralContext:
    """Passed to a card provider when a ``next_card`` button is clicked."""

    client: Any
    interaction: Any
    origin: str
    group_openid: str
    member_openid: str
    session_id: str = ""
    params: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ActionSpec:
    action_id: str
    title: str
    description: str
    owner: str
    default_permission: str
    callback: ActionCallback


class ActionRegistry:
    #: Read by ``get_action_registry`` to decide whether a registry left behind
    #: by a *previous incarnation of this module* is still usable.
    protocol = REGISTRY_PROTOCOL

    def __init__(self) -> None:
        self._actions: dict[str, ActionSpec] = {}
        self._lock = asyncio.Lock()

    def register(self, spec: ActionSpec) -> None:
        prior = self._actions.get(spec.action_id)
        if prior is not None and prior.owner != spec.owner:
            raise ValueError(
                f"action_id {spec.action_id} already owned by {prior.owner}"
            )
        self._actions[spec.action_id] = spec

    def unregister_owner(self, owner: str) -> None:
        for action_id, spec in list(self._actions.items()):
            if spec.owner == owner:
                del self._actions[action_id]

    def catalog(self) -> list[dict[str, str]]:
        def sort_key(spec: ActionSpec) -> tuple[int, str, str]:
            # Command actions use hashed action_id values, so sorting by id makes
            # the "直接执行" dropdown look random.  Sort registered commands by
            # their human-readable title/description instead; keep built-in Hub
            # actions above command actions.
            is_command = spec.owner.endswith(".commands")
            return (1 if is_command else 0, spec.title, spec.description)

        return [
            {
                "id": spec.action_id,
                "title": spec.title,
                "description": spec.description,
                "owner": spec.owner,
                "default_permission": spec.default_permission,
            }
            for spec in sorted(self._actions.values(), key=sort_key)
        ]

    def contains(self, action_id: str) -> bool:
        return action_id in self._actions

    async def execute(
        self,
        action_id: str,
        context: ActionContext,
        params: dict[str, Any],
    ) -> int:
        spec = self._actions.get(action_id)
        if spec is None:
            return 1
        # Registry mutation is synchronous and callbacks execute outside locks.
        result = int(await spec.callback(context, params))
        return result if result in {0, 1, 2, 3, 4, 5} else 1


def is_compatible_registry(registry: object) -> bool:
    """Duck-typed compatibility check for a registry from another incarnation.

    ``isinstance`` is **wrong** here and was a real bug: AstrBot's plugin
    reload deletes every ``data.plugins.<hub>.*`` entry from ``sys.modules``
    (star_manager ``_purge_modules``) and re-imports the package, producing a
    brand-new ``ActionRegistry`` *class object*. The registry parked on
    ``builtins`` was built by the previous class, so ``isinstance`` returns
    False, the Hub silently created an empty registry, and every Action that
    third-party plugins had registered vanished -- the panel then reported
    "外部插件 0 个" even though those plugins were loaded and healthy.

    So identity is checked by protocol number plus the method surface actually
    called, which is what "same class" was ever meant to stand for.
    """
    if registry is None:
        return False
    if getattr(registry, "protocol", None) != REGISTRY_PROTOCOL:
        return False
    return all(callable(getattr(registry, name, None)) for name in _REGISTRY_SURFACE)


def get_action_registry() -> ActionRegistry:
    registry = getattr(builtins, _REGISTRY_KEY, None)
    if not is_compatible_registry(registry):
        registry = ActionRegistry()
        setattr(builtins, _REGISTRY_KEY, registry)
    return registry
