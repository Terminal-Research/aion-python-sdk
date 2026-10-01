"""Whose framework state a request uses: its caller's, or its gateway conversation's."""

from a2a.server.context import ServerCallContext
from a2a.server.owner_resolver import resolve_user_scope
from a2a.server.routes.common import StarletteUser
from starlette.authentication import SimpleUser

from aion.server.agent.adapters import StateScope
from aion.server.auth import CallerCredentials
from tests.support.aion_tokens import EDGE_ENVIRONMENT_ID, OWNER_AGENT_IDENTITY_ID
from tests.unit.support.tokens import AION_KEY, verifier


async def _context(token: str) -> ServerCallContext:
    built = verifier()
    await built.load()
    caller = await built.verify(token)
    return ServerCallContext(user=StarletteUser(caller), state={"auth": CallerCredentials(caller)})


async def test_a_session_keeps_its_own_state() -> None:
    context = await _context(AION_KEY.session_token("f9d7daea-df95-4111-8533-5d6f043edaf9"))

    scope = StateScope.for_call("agent-1", context, resolve_user_scope)

    assert scope.gateway is None
    assert scope.state_owner == scope.owner_scope == context.user.user_name


async def test_every_participant_of_a_gateway_conversation_names_one_state() -> None:
    alice = StateScope.for_call("agent-1", await _context(AION_KEY.invocation_token("alice")), resolve_user_scope)
    bob = StateScope.for_call("agent-1", await _context(AION_KEY.invocation_token("bob")), resolve_user_scope)

    assert alice.owner_scope != bob.owner_scope
    assert alice.key_for("ctx") == bob.key_for("ctx")
    assert alice.gateway == (OWNER_AGENT_IDENTITY_ID, EDGE_ENVIRONMENT_ID)
    assert alice.state_owner == f"aion.gateway:{OWNER_AGENT_IDENTITY_ID}:{EDGE_ENVIRONMENT_ID}"


async def test_the_gateway_state_is_per_agent_identity_and_edge() -> None:
    base = await _context(AION_KEY.invocation_token("alice"))
    other_edge = await _context(
        AION_KEY.invocation_token("alice", edge_agent_environment_id="2b4c6d8e-0f1a-4b3c-8d5e-7f9a1b2c3d4e")
    )

    keys = {
        StateScope.for_call("agent-1", base, resolve_user_scope).key_for("ctx"),
        StateScope.for_call("agent-1", other_edge, resolve_user_scope).key_for("ctx"),
        StateScope.for_call("agent-2", base, resolve_user_scope).key_for("ctx"),
    }

    assert len(keys) == 3


def test_metadata_or_an_application_user_never_makes_state_shared() -> None:
    context = ServerCallContext(
        user=StarletteUser(SimpleUser(f"aion.gateway:{OWNER_AGENT_IDENTITY_ID}:{EDGE_ENVIRONMENT_ID}")),
        state={"auth": None},
    )

    assert StateScope.for_call("agent-1", context, resolve_user_scope).gateway is None
