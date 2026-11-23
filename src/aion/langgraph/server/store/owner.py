"""The server's store as a graph sees it: every namespace under the turn's agent and state owner.

A LangGraph store is one for every thread: ``runtime.store`` keeps a graph's
memory across conversations. The store the server gives a graph is that of
every user of the agent, and of every agent on the same database, so the
server keeps each one's data apart. Every namespace a graph names is kept
under two labels it never sees - the agent and the state owner of the turn's
conversation, the same two its checkpoints are keyed by - and every
namespace a result carries comes back without them. A graph writes
``("memories",)`` and reads it back as ``("memories",)``; another user of the
agent reads nothing of it, and neither does another agent.

The labels come from the run's ``thread_id``, the key ``StateScope.key_for``
built for the turn, which LangGraph keeps in the config of every node and
task the turn runs. A call with no such key - outside a run of the server -
reaches no data: it raises ``StoreOwnerUnavailableError``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any
from urllib.parse import quote

from langgraph.config import get_config
from langgraph.store.base import (
    BaseStore,
    GetOp,
    Item,
    ListNamespacesOp,
    MatchCondition,
    Op,
    PutOp,
    Result,
    SearchItem,
    SearchOp,
)

from aion.core.exceptions import AionError
from aion.server.agent.adapters import state_key_parts

__all__ = ["OwnerScopedStore", "StoreOwnerUnavailableError"]


class StoreOwnerUnavailableError(AionError, RuntimeError):
    """The store was used outside a turn of the server, where there is no owner to keep the data under."""


class OwnerScopedStore(BaseStore):
    """A store that keeps the data of each agent and state owner apart, under labels the graph never sees.

    Wraps the store that holds the data; a graph uses it as any LangGraph
    store, through ``runtime.store``. Everything goes through ``batch`` and
    ``abatch``, which add the labels to the namespace of each operation and
    remove them from what comes back.
    """

    __slots__ = ("_store",)

    supports_ttl = False

    def __init__(self, store: BaseStore) -> None:
        self._store = store

    @property
    def store(self) -> BaseStore:
        """The store that holds the data, every agent's and owner's."""
        return self._store

    def batch(self, ops: Iterable[Op]) -> list[Result]:
        """Run ``ops`` under the turn's agent and owner."""
        prefix = _turn_prefix()
        ops = list(ops)
        return _unscoped(prefix, ops, self._store.batch([_scoped(prefix, op) for op in ops]))

    async def abatch(self, ops: Iterable[Op]) -> list[Result]:
        """Run ``ops`` under the turn's agent and owner."""
        prefix = _turn_prefix()
        ops = list(ops)
        return _unscoped(prefix, ops, await self._store.abatch([_scoped(prefix, op) for op in ops]))


def _turn_prefix() -> tuple[str, str]:
    """The labels of the turn's agent and state owner, from the run's ``thread_id``."""
    try:
        thread_id = get_config().get("configurable", {}).get("thread_id")
    except RuntimeError:
        thread_id = None
    parts = state_key_parts(thread_id) if isinstance(thread_id, str) else None
    if parts is None or not parts[0] or not parts[1]:
        raise StoreOwnerUnavailableError(
            "The store keeps each user's data apart, and this call has no user to keep it under: "
            "the server's store is reachable only inside a turn of the server"
        )
    agent_id, state_owner, _ = parts
    return _label(agent_id), _label(state_owner)


def _label(value: str) -> str:
    """``value`` as one namespace label: percent-encoded, ``.`` included, which a label may not hold."""
    return quote(value, safe="").replace(".", "%2E")


def _scoped(prefix: tuple[str, ...], op: Op) -> Op:
    """``op`` with every namespace it names under ``prefix``."""
    if isinstance(op, (GetOp, PutOp)):
        return op._replace(namespace=(*prefix, *op.namespace))
    if isinstance(op, SearchOp):
        return op._replace(namespace_prefix=(*prefix, *op.namespace_prefix))
    if isinstance(op, ListNamespacesOp):
        conditions = [
            MatchCondition(match_type="prefix", path=(*prefix, *condition.path))
            if condition.match_type == "prefix"
            else condition
            for condition in op.match_conditions or ()
        ]
        if not any(condition.match_type == "prefix" for condition in conditions):
            conditions.insert(0, MatchCondition(match_type="prefix", path=prefix))
        max_depth = None if op.max_depth is None else op.max_depth + len(prefix)
        return op._replace(match_conditions=tuple(conditions), max_depth=max_depth)
    raise ValueError(f"Unknown store operation {type(op).__name__}")


def _unscoped(prefix: tuple[str, ...], ops: Sequence[Op], results: Sequence[Result]) -> list[Result]:
    """``results`` with ``prefix`` taken off every namespace they carry."""
    return [_unscoped_result(prefix, op, result) for op, result in zip(ops, results, strict=True)]


def _unscoped_result(prefix: tuple[str, ...], op: Op, result: Result) -> Result:
    if isinstance(op, GetOp):
        return None if result is None else _unscoped_item(prefix, result)
    if isinstance(op, SearchOp):
        return [_unscoped_item(prefix, item) for item in result]
    if isinstance(op, ListNamespacesOp):
        return [tuple(namespace[len(prefix):]) for namespace in result]
    return result


def _unscoped_item(prefix: tuple[str, ...], item: Any) -> Item:
    """A copy of ``item`` with ``prefix`` taken off its namespace."""
    namespace = tuple(item.namespace[len(prefix):])
    if isinstance(item, SearchItem):
        return SearchItem(
            namespace=namespace,
            key=item.key,
            value=item.value,
            created_at=item.created_at,
            updated_at=item.updated_at,
            score=item.score,
        )
    return Item(
        value=item.value,
        key=item.key,
        namespace=namespace,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )
