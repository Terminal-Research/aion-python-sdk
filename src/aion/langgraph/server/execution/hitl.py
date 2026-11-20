"""The answer ``HumanInTheLoopMiddleware`` takes to one question, as a JSON Schema.

LangChain's ``HumanInTheLoopMiddleware`` stops an agent before the tool
calls it guards. In its default, batched mode it asks one question for all
of them, an ``interrupt()`` whose value is a ``HITLRequest``: the calls to
review (``action_requests``) and the decisions each tool allows
(``review_configs``). The answer is a ``HITLResponse``,
``{"decisions": [...]}``, one decision per call in the same order. LangGraph
carries no schema for that question, unlike the middleware's ``per_call``
mode, whose interrupt states one itself.

``response_schema`` builds that schema from LangChain's own types and the
question's configuration: exactly one decision per call, each one of the
decisions its tool allows. The question goes out with it as its
``responseSchema``, so a client knows how to answer, and the server refuses
an answer that does not fit before the agent runs.
"""

from __future__ import annotations

from functools import cache
from typing import Any, Optional

from langchain.agents.middleware.human_in_the_loop import HITLResponse
from pydantic import TypeAdapter

__all__ = ["response_schema"]

_REQUEST_KEYS = frozenset({"action_requests", "review_configs"})

_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def response_schema(value: Any) -> Optional[dict[str, Any]]:
    """The JSON Schema of the answer to a ``HumanInTheLoopMiddleware`` question, or None for any other value.

    Args:
        value: An ``interrupt()`` value. A ``HITLRequest`` is a dict with
            exactly ``action_requests`` and ``review_configs``.
    """
    if not _is_request(value):
        return None
    definitions, by_type = _decisions()
    allowed = {config["action_name"]: config["allowed_decisions"] for config in value["review_configs"]}
    calls = [
        {"anyOf": refs} if (refs := _refs(allowed.get(action["name"], ()), by_type)) else False
        for action in value["action_requests"]
    ]
    return {
        "$schema": _SCHEMA_DIALECT,
        "type": "object",
        "properties": {
            "decisions": {
                "type": "array",
                "prefixItems": calls,
                "minItems": len(calls),
                "maxItems": len(calls),
            },
        },
        "required": ["decisions"],
        "$defs": definitions,
    }


def _is_request(value: Any) -> bool:
    if not isinstance(value, dict) or set(value) != _REQUEST_KEYS:
        return False
    actions, configs = value["action_requests"], value["review_configs"]
    return (
        isinstance(actions, list)
        and isinstance(configs, list)
        and bool(actions)
        and all(isinstance(action, dict) and isinstance(action.get("name"), str) for action in actions)
        and all(
            isinstance(config, dict)
            and isinstance(config.get("action_name"), str)
            and isinstance(config.get("allowed_decisions"), list)
            for config in configs
        )
    )


def _refs(decision_types: Any, by_type: dict[str, str]) -> list[dict[str, str]]:
    return [{"$ref": f"#/$defs/{by_type[kind]}"} for kind in decision_types if kind in by_type]


@cache
def _decisions() -> tuple[dict[str, Any], dict[str, str]]:
    """The definitions of ``HITLResponse``'s JSON Schema, and the definition of each decision type.

    A decision is a ``TypedDict`` whose ``type`` is a literal - ``approve``,
    ``edit``, ``reject`` and, in later LangChain releases, ``respond`` - so
    its definition is found by that constant, whatever the installed release
    names it.
    """
    definitions = TypeAdapter(HITLResponse).json_schema().get("$defs", {})
    by_type: dict[str, str] = {}
    for name, definition in definitions.items():
        kind = definition.get("properties", {}).get("type", {}).get("const")
        if isinstance(kind, str):
            by_type[kind] = name
    return definitions, by_type
