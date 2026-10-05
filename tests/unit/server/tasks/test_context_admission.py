"""Who a request holds its context as, and the in-process reservations."""

import asyncio

from a2a.server.context import ServerCallContext
from a2a.server.owner_resolver import resolve_user_scope
from a2a.server.routes.common import StarletteUser
from starlette.authentication import SimpleUser

from aion.server.auth import CallerCredentials, Principal
from aion.server.tasks.admission import (
    DELETED,
    DELETING,
    ContextHolder,
    InMemoryContextAdmission,
    holder_of,
)
from tests.support.aion_tokens import EDGE_ENVIRONMENT_ID, OWNER_AGENT_IDENTITY_ID
from tests.unit.support.tokens import AION_KEY, verifier


async def _caller(token: str):
    built = verifier()
    await built.load()
    return await built.verify(token)


def _context(user, credentials=None) -> ServerCallContext:
    """A call context as a2a-sdk builds it: the user wrapped, the credentials as they were."""
    state = {"auth": credentials} if credentials is not None else {}
    return ServerCallContext(user=StarletteUser(user), state=state)


async def test_an_invocation_holds_its_gateway_conversation() -> None:
    caller = await _caller(AION_KEY.invocation_token("alice"))

    holder = holder_of(_context(caller, CallerCredentials(caller)), resolve_user_scope)

    assert holder == ContextHolder.shared(OWNER_AGENT_IDENTITY_ID, EDGE_ENVIRONMENT_ID)


async def test_a_gateway_guest_holds_the_conversation_too() -> None:
    caller = await _caller(
        AION_KEY.invocation_token("f9d7daea-df95-4111-8533-5d6f043edaf9", principal_type="AnonymousSession",
                                  assurance="session")
    )

    assert holder_of(_context(caller, CallerCredentials(caller)), resolve_user_scope).kind == "shared"


async def test_a_session_holds_a_private_context_by_its_subject() -> None:
    caller = await _caller(AION_KEY.session_token("f9d7daea-df95-4111-8533-5d6f043edaf9"))

    holder = holder_of(_context(caller, CallerCredentials(caller)), resolve_user_scope)

    assert holder == ContextHolder.private(Principal("AnonymousSession", "f9d7daea-df95-4111-8533-5d6f043edaf9").subject)


def test_an_application_user_holds_a_private_context_by_its_name() -> None:
    assert holder_of(_context(SimpleUser("alice")), resolve_user_scope) == ContextHolder.private("alice")


def test_a_caller_without_an_owner_holds_nothing() -> None:
    assert holder_of(ServerCallContext(), lambda context: None) is None
    assert holder_of(ServerCallContext(), lambda context: "") is None


async def test_the_first_holder_keeps_the_context() -> None:
    admission = InMemoryContextAdmission()
    alice, bob = ContextHolder.private("alice"), ContextHolder.private("bob")

    assert await admission.admit("ctx", alice, "alice")
    assert not await admission.admit("ctx", bob, "bob")
    assert await admission.admit("ctx", alice, "alice")
    assert await admission.admit("other", bob, "bob")


async def test_concurrent_first_holders_get_one_admission() -> None:
    admission = InMemoryContextAdmission()
    holders = [ContextHolder.private(name) for name in ("a", "b", "c", "d")]

    results = await asyncio.gather(*(admission.admit("ctx", holder, holder.owner_scope) for holder in holders))

    assert results.count(True) == 1


async def test_an_admitted_member_is_bound_and_a_refused_one_is_not() -> None:
    admission = InMemoryContextAdmission()
    gateway = ContextHolder.shared("identity", "environment")

    assert await admission.admit("ctx", gateway, "alice")
    assert await admission.admit("ctx", gateway, "bob")
    assert not await admission.admit("ctx", ContextHolder.private("carol"), "carol")

    assert set(admission.entries["ctx"].bindings) == {"alice", "bob"}


async def test_a_caller_without_individual_access_is_admitted_unbound() -> None:
    admission = InMemoryContextAdmission()

    assert await admission.admit("ctx", ContextHolder.shared("identity", "environment"), None)
    assert admission.entries["ctx"].bindings == {}


async def test_a_context_being_deleted_admits_nobody() -> None:
    admission = InMemoryContextAdmission()
    alice = ContextHolder.private("alice")
    await admission.admit("ctx", alice, "alice")
    admission.entries["ctx"].state = DELETING

    assert not await admission.admit("ctx", alice, "alice")


async def test_a_deleted_context_is_reserved_afresh() -> None:
    """Nothing of a deleted context remains, so its ID starts a new conversation for whoever comes."""
    admission = InMemoryContextAdmission()
    await admission.admit("ctx", ContextHolder.private("alice"), "alice")
    admission.entries["ctx"].state = DELETED

    assert await admission.admit("ctx", ContextHolder.private("bob"), "bob")
    assert admission.entries["ctx"].holder == ContextHolder.private("bob")
    assert set(admission.entries["ctx"].bindings) == {"bob"}
