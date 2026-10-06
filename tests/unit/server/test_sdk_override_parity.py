"""Contract tests binding Aion's subclasses to the a2a-sdk classes they extend.

Aion overrides a number of a2a-sdk extension points, and several of those
overrides reimplement the base body rather than delegating to ``super()``.
That style is deliberate — it is how ``AionTaskManager`` and the per-task push
projection get injected — but it means an SDK upgrade can silently desynchronise
the two sides: the base gains a parameter, starts passing it at a call site
Aion does not control, and the override rejects it at runtime - for
``DefaultRequestHandlerV2._setup_active_task`` passing ``initial_message=`` to
``ActiveTaskRegistry.get_or_create``, that would break every ``message/send``
and ``message/stream`` call while a unit suite without the real registry
attached stays green.

These tests assert the coupling directly instead of relying on some other
test to happen to traverse it.
"""

import ast
import hashlib
import inspect

import pytest
from a2a.server.agent_execution import AgentExecutor, RequestContextBuilder
from a2a.server.agent_execution.active_task_registry import ActiveTaskRegistry
from a2a.server.cluster import VersionedTaskStore
from a2a.server.request_handlers import DefaultRequestHandlerV2
from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher
from a2a.server.tasks import TaskManager, TaskStore
from a2a.server.tasks.base_push_notification_sender import BasePushNotificationSender
from a2a.server.tasks.push_notification_sender import PushNotificationSender
from a2a.types import Message, Role
from unittest.mock import AsyncMock, Mock, patch

from aion.server.agent.execution import active_task_registry as active_task_registry_module
from aion.server.agent.execution.active_task_registry import AionActiveTaskRegistry
from aion.server.agent.execution.scope import clear_execution_scope, init_execution_scope
from aion.server.agent.execution.request_context_builder import AionRequestContextBuilder
from aion.server.agent.execution.request_executor import AionAgentRequestExecutor
from aion.server.core.app.handlers.jsonrpc_dispatcher import AionJsonRpcDispatcher
from aion.server.core.app.handlers.request_handler import AionRequestHandler
from aion.server.tasks.push_sender import AionPushNotificationSender
from aion.server.tasks.stores.in_memory_task_store import InMemoryTaskStore
from aion.server.tasks.stores.postgres_task_store import PostgresTaskStore
from aion.server.tasks.stores.postgres_versioned_task_store import PostgresVersionedTaskStore
from aion.server.tasks.task_manager import AionTaskManager
from aion.server.tasks.terminal_push_sender import TerminalTaskPushSender

# (override class, a2a-sdk base class, overridden method) triples that the
# server depends on. Extend this table whenever a new SDK extension point is
# subclassed.
OVERRIDES = [
    (AionActiveTaskRegistry, ActiveTaskRegistry, "get"),
    (AionActiveTaskRegistry, ActiveTaskRegistry, "get_or_create"),
    (AionActiveTaskRegistry, ActiveTaskRegistry, "_on_active_task_cleanup"),
    (AionActiveTaskRegistry, ActiveTaskRegistry, "aclose"),
    (AionActiveTaskRegistry, ActiveTaskRegistry, "_remove_task"),
    (AionTaskManager, TaskManager, "invalidate"),
    (AionTaskManager, TaskManager, "get_task"),
    (AionTaskManager, TaskManager, "ensure_task_id"),
    (AionTaskManager, TaskManager, "_save_task"),
    (AionTaskManager, TaskManager, "process"),
    (AionRequestHandler, DefaultRequestHandlerV2, "_setup_active_task"),
    (AionRequestHandler, DefaultRequestHandlerV2, "on_get_task"),
    (AionRequestHandler, DefaultRequestHandlerV2, "on_list_tasks"),
    (AionRequestHandler, DefaultRequestHandlerV2, "on_cancel_task"),
    (AionRequestHandler, DefaultRequestHandlerV2, "on_message_send"),
    (AionRequestHandler, DefaultRequestHandlerV2, "on_message_send_stream"),
    (AionRequestHandler, DefaultRequestHandlerV2, "on_subscribe_to_task"),
    (AionRequestHandler, DefaultRequestHandlerV2, "on_create_task_push_notification_config"),
    (AionRequestHandler, DefaultRequestHandlerV2, "on_get_task_push_notification_config"),
    (AionRequestHandler, DefaultRequestHandlerV2, "on_list_task_push_notification_configs"),
    (AionRequestHandler, DefaultRequestHandlerV2, "on_delete_task_push_notification_config"),
    (AionJsonRpcDispatcher, JsonRpcDispatcher, "handle_requests"),
    (AionJsonRpcDispatcher, JsonRpcDispatcher, "_process_streaming_request"),
    (AionJsonRpcDispatcher, JsonRpcDispatcher, "_create_response"),
    (AionRequestContextBuilder, RequestContextBuilder, "build"),
    (AionAgentRequestExecutor, AgentExecutor, "execute"),
    (AionAgentRequestExecutor, AgentExecutor, "cancel"),
    (TerminalTaskPushSender, PushNotificationSender, "send_notification"),
    (
        AionPushNotificationSender,
        BasePushNotificationSender,
        "_dispatch_notification",
    ),
    (
        AionPushNotificationSender,
        BasePushNotificationSender,
        "send_notification",
    ),
    (InMemoryTaskStore, TaskStore, "save"),
    (InMemoryTaskStore, TaskStore, "get"),
    (InMemoryTaskStore, TaskStore, "delete"),
    (InMemoryTaskStore, TaskStore, "list"),
    (PostgresTaskStore, TaskStore, "save"),
    (PostgresTaskStore, TaskStore, "get"),
    (PostgresTaskStore, TaskStore, "delete"),
    (PostgresTaskStore, TaskStore, "list"),
    (PostgresVersionedTaskStore, VersionedTaskStore, "save"),
    (PostgresVersionedTaskStore, VersionedTaskStore, "get"),
    (PostgresVersionedTaskStore, VersionedTaskStore, "delete"),
    (PostgresVersionedTaskStore, VersionedTaskStore, "list"),
]

# The a2a-sdk methods whose bodies an override copies and changes rather than
# calling: the source of each, as one a2a-sdk release ships it, hashed
# (sha256 of ``inspect.getsource``). A release that changes such a body -
# keeping its signature, so the tests above stay green - leaves the copy here
# behind it; the hash is what notices. One entry per a2a-sdk release in the
# supported range whose copy has been compared with the override.
COPIED_BODIES = {
    (ActiveTaskRegistry, "get"): {
        "1.2.2": "038ae198bdf3d13402709dd84f42c88afcbcb0786d4ab1cc03dcb21114218744",
    },
    (ActiveTaskRegistry, "get_or_create"): {
        "1.2.2": "08bcf2740e25ab6e98557ae2003ae98c3a77b0263b5b7e333976ce9b0f8d41cc",
    },
    (ActiveTaskRegistry, "_on_active_task_cleanup"): {
        "1.2.2": "40595a22c2b71e62ebdfe9d816e7fd4a386deeb08ad4b7383d0cb8055651bc91",
    },
    (BasePushNotificationSender, "send_notification"): {
        "1.2.2": "18d0ee14c71993e519652a6e87f5d3bca610987c739a3196312ef448da801a95",
    },
    (BasePushNotificationSender, "_dispatch_notification"): {
        "1.2.2": "1bae1aa38d4cce000e323a27bf42fc131c590603a7b786dec2f85b8d22e759ee",
    },
}

# Aion-only TaskStore operations must not silently become overrides when the
# a2a-sdk grows its storage contract. Such a collision requires an explicit
# compatibility decision during the dependency upgrade.
AION_TASK_STORE_EXTENSIONS = [
    "get_context_tasks",
    "get_context_last_task",
    "write",
    "get_in_session",
    "tasks_in_context",
    "retire_context",
]


@pytest.mark.parametrize("method_name", AION_TASK_STORE_EXTENSIONS)
def test_task_store_extensions_do_not_shadow_sdk(method_name):
    assert method_name not in TaskStore.__dict__, (
        f"BaseTaskStore.{method_name} now shadows an a2a-sdk TaskStore method; "
        "review both contracts before upgrading the SDK."
    )


def _accepts_arbitrary_keywords(signature: inspect.Signature) -> bool:
    """Reports whether a signature absorbs unknown keyword arguments.

    Args:
        signature: Signature of the overriding method.

    Returns:
        True if the signature declares ``**kwargs``, in which case it can
        absorb any parameter the base class introduces.
    """
    return any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )


@pytest.mark.parametrize(
    ("override_cls", "base_cls", "method_name"),
    OVERRIDES,
    ids=[f"{o.__name__}.{m}" for o, _, m in OVERRIDES],
)
def test_override_accepts_every_base_parameter(override_cls, base_cls, method_name):
    """Every parameter the SDK base declares must be accepted by the override.

    The SDK calls these methods through the base class, so any parameter the
    base knows about can arrive at the override. An override that omits one
    raises ``TypeError`` at request time rather than at import time, which is
    why this is asserted statically.
    """
    base_method = getattr(base_cls, method_name)
    override_method = getattr(override_cls, method_name)

    assert override_method is not base_method, (
        f"{override_cls.__name__}.{method_name} no longer overrides "
        f"{base_cls.__name__}.{method_name}; drop it from OVERRIDES."
    )

    base_signature = inspect.signature(base_method)
    override_signature = inspect.signature(override_method)

    if _accepts_arbitrary_keywords(override_signature):
        return

    missing = set(base_signature.parameters) - set(override_signature.parameters)
    assert not missing, (
        f"{override_cls.__name__}.{method_name} does not accept "
        f"{sorted(missing)}, which {base_cls.__name__}.{method_name} declares. "
        f"The SDK can pass these, so the override must take them."
    )


@pytest.mark.parametrize(
    ("override_cls", "base_cls", "method_name"),
    OVERRIDES,
    ids=[f"{o.__name__}.{m}" for o, _, m in OVERRIDES],
)
def test_override_preserves_positional_parameter_order(
    override_cls, base_cls, method_name
):
    """Shared parameters must keep the base's relative order.

    The SDK passes some of these positionally, so reordering silently binds a
    value to the wrong parameter instead of raising.
    """
    base_signature = inspect.signature(getattr(base_cls, method_name))
    override_signature = inspect.signature(getattr(override_cls, method_name))

    positional_kinds = (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )
    base_positional = [
        name
        for name, parameter in base_signature.parameters.items()
        if parameter.kind in positional_kinds
    ]
    shared_in_override = [
        name
        for name, parameter in override_signature.parameters.items()
        if parameter.kind in positional_kinds and name in base_positional
    ]
    expected = [name for name in base_positional if name in shared_in_override]

    assert shared_in_override == expected, (
        f"{override_cls.__name__}.{method_name} reorders parameters relative to "
        f"{base_cls.__name__}.{method_name}: expected {expected}, "
        f"got {shared_in_override}."
    )


@pytest.fixture
def execution_scope():
    """Provides the execution scope the registry stores the task manager in."""
    init_execution_scope()
    yield
    clear_execution_scope()


async def test_get_or_create_threads_initial_message_into_task_manager(execution_scope):
    """The inbound user message must reach the task manager that records it.

    ``AionActiveTaskRegistry.get_or_create`` reimplements the base body and so
    is responsible for forwarding ``initial_message`` itself. The SDK supplies
    the real message and de-duplicates it downstream by ``message_id``, so
    dropping it here would lose the user turn from task history rather than
    fail loudly.
    """
    registry = AionActiveTaskRegistry(
        agent_executor=Mock(),
        task_store=InMemoryTaskStore(),
        push_sender=None,
    )
    message = Message(message_id="msg-initial", role=Role.ROLE_USER)

    with patch(
        "aion.server.agent.execution.active_task_registry.ActiveTask"
    ) as active_task_cls:
        active_task_cls.return_value.start = AsyncMock()
        await registry.get_or_create(
            "task-initial-message",
            call_context=Mock(),
            context_id="ctx-initial-message",
            initial_message=message,
        )

    bound_manager = active_task_cls.call_args.kwargs["task_manager"]
    assert bound_manager._initial_message is message


async def test_get_or_create_refuses_work_once_registry_is_closed():
    """A closed registry must not hand out new tasks.

    ``ActiveTaskRegistry.aclose()`` marks the registry
    closed so shutdown can drain it without racing new arrivals. Since the Aion
    override reimplements ``get_or_create``, it has to honour that flag itself,
    otherwise a task registered during shutdown is never drained.
    """
    registry = AionActiveTaskRegistry(
        agent_executor=Mock(),
        task_store=InMemoryTaskStore(),
        push_sender=None,
    )
    await registry.aclose()

    with pytest.raises(RuntimeError, match="closed"):
        await registry.get_or_create("task-after-close", call_context=Mock())


def test_no_critical_section_of_the_registry_awaits():
    """The registry's ``threading.RLock`` is never held across an ``await``.

    An ``RLock`` held across an ``await`` lets another coroutine on the same
    thread re-enter it and so excludes nothing. Every section the override
    guards is therefore synchronous; this fails the day one is not.
    """
    tree = ast.parse(inspect.getsource(active_task_registry_module))
    sections = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.With)
        and ast.unparse(node.items[0].context_expr) == "self._lock"
    ]
    awaiting = [
        section.lineno
        for section in sections
        for statement in section.body
        for node in ast.walk(statement)
        if isinstance(node, (ast.Await, ast.AsyncFor, ast.AsyncWith))
    ]

    assert sections
    assert awaiting == [], f"critical sections awaiting at lines {awaiting}"


async def test_a_message_to_a_terminal_task_is_refused_as_the_base_refuses_it():
    """A send into a task with an outcome answers ``UnsupportedOperationError``.

    The override builds the ``ActiveTask`` and leaves the check to the base
    ``ActiveTask.start``, which refuses a terminal task naming its state.
    """
    from a2a.auth.user import User
    from a2a.server.context import ServerCallContext
    from a2a.types import Task, TaskState, TaskStatus
    from a2a.utils.errors import UnsupportedOperationError

    class _Caller(User):
        @property
        def is_authenticated(self) -> bool:
            return True

        @property
        def user_name(self) -> str:
            return "alice"

    store = InMemoryTaskStore()
    context = ServerCallContext(user=_Caller())
    await store.save(
        Task(
            id="task-done",
            context_id="ctx-1",
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        ),
        context,
    )
    registry = AionActiveTaskRegistry(agent_executor=Mock(), task_store=store, push_sender=None)

    init_execution_scope()
    try:
        with pytest.raises(UnsupportedOperationError, match="TASK_STATE_COMPLETED"):
            await registry.get_or_create(
                "task-done",
                call_context=context,
                context_id="ctx-1",
                create_task_if_missing=True,
            )
    finally:
        clear_execution_scope()
    await registry.aclose()


def _source_hash(function) -> str:
    return hashlib.sha256(inspect.getsource(function).encode()).hexdigest()


@pytest.mark.parametrize(
    ("base_cls", "method_name"),
    list(COPIED_BODIES),
    ids=[f"{base.__name__}.{method}" for base, method in COPIED_BODIES],
)
def test_a_copied_a2a_sdk_body_is_the_one_the_copy_was_compared_with(base_cls, method_name):
    """The installed a2a-sdk ships the body the override was last compared with.

    A failure means a2a-sdk changed the method. Compare its new body with the
    Aion override, carry the change over where it applies, and add the new
    hash under the release that ships it.
    """
    from importlib.metadata import version

    installed = _source_hash(getattr(base_cls, method_name))
    approved = COPIED_BODIES[(base_cls, method_name)]

    assert installed in approved.values(), (
        f"a2a-sdk {version('a2a-sdk')} changed {base_cls.__name__}.{method_name} "
        f"(sha256 {installed}); compare it with the Aion override, then record "
        f"the hash in COPIED_BODIES"
    )


def test_every_copied_body_is_an_override_in_the_table():
    """A hashed method is one Aion overrides; dropping the override drops the hash."""
    overridden = {(base, method) for _, base, method in OVERRIDES}

    assert set(COPIED_BODIES) <= overridden
