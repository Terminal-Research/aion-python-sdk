"""The SDK's evolution wrapper: its options, what it needs to be created, what it refuses and what it hands the toolkit.

The toolkit is the stand-in of ``tests.support.scripted_executor.fake_toolkit``
(``fake_toolkit``), or no toolkit at all (``no_toolkit``): the SDK's tests
never install the real one.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Optional

import pytest
from a2a.auth.user import User
from a2a.server.agent_execution import RequestContext
from a2a.server.context import ServerCallContext
from a2a.types import Artifact, Part, SendMessageRequest, Task, TaskArtifactUpdateEvent, TaskState, TaskStatus
from google.protobuf.json_format import MessageToDict
from pydantic import ValidationError

from aion.core.a2a import codes
from aion.core.config import AgentConfig
from aion.core.constants.a2a import BEHAVIOR_EVOLUTION_EXTENSION_URI_V1, DAEMON_EXTENSION_URI_V1
from aion.core.exceptions import ConfigurationError, ExtensionRejection
from aion.core.principal import Principal
from aion.core.runtime.context.models import AionRuntimeContext
from aion.server.agent.adapters import ExecutionConfig
from aion.server.agent.execution.context.attribution import callback_attribution
from aion.server.agent.execution.extensions import create_extension_executors, register_extensions
from aion.server.agent.execution.extensions import evolution
from aion.server.agent.execution.extensions import model_service as model_access
from aion.server.agent.execution.extensions.evolution import (
    EVOLUTION_EXTENSION,
    Caller,
    EvolutionExecutor,
    EvolutionOptions,
    Provider,
)
from aion.server.auth import Assurance, AuthenticatedCaller, CallerCredentials, CredentialKind
from aion.server.auth.caller import GatewayCoordinates

from tests.support.conversation import InMemoryConversations, install
from tests.support.scripted_executor.contract import EVOLUTION_URI
from tests.support.scripted_executor.script import COMPLETED, Refusal, scripted_message

URI = BEHAVIOR_EVOLUTION_EXTENSION_URI_V1
CONTEXT_ID = "ctx-1"
TASK_ID = "task-1"
ROUTED = "aion:routedExtension"

pytestmark = pytest.mark.usefixtures("runtime_provider")


def test_the_contract_uri_is_the_sdks_constant() -> None:
    assert URI == EVOLUTION_URI == EVOLUTION_EXTENSION.uri


# -- options ----------------------------------------------------------------------


def test_the_defaults_let_the_platform_call_and_use_the_aion_model_service() -> None:
    options = EvolutionOptions()

    assert (options.callers, options.provider) == ((Caller.PLATFORM,), Provider.AION)


def test_the_options_read_as_aion_yaml_writes_them() -> None:
    options = EvolutionOptions.model_validate({"callers": ["platform", "anonymous_session"], "provider": "custom"})

    assert (options.callers, options.provider) == ((Caller.PLATFORM, Caller.ANONYMOUS_SESSION), Provider.CUSTOM)


@pytest.mark.parametrize(
    "given",
    [{"callers": []}, {"callers": ["application_user"]}, {"provider": "openai"}, {"model": "x"}],
    ids=["nobody", "unknown-caller", "unknown-provider", "unknown-option"],
)
def test_options_the_extension_does_not_allow_are_refused(given) -> None:
    with pytest.raises(ValidationError):
        EvolutionOptions.model_validate(given)


# -- creating the executor ------------------------------------------------------------------


def executor(**options: Any) -> EvolutionExecutor:
    return EvolutionExecutor(options)


def test_without_the_toolkit_the_executor_cannot_be_created_and_says_how_to_install_it(no_toolkit) -> None:
    with pytest.raises(ConfigurationError) as error:
        executor(provider="local_session")

    assert str(error.value) == (
        f"the extension {URI} cannot run on this server: the behavior-evolution toolkit is not installed "
        "(pip install aion-toolkit-behavior-evolution)"
    )


@pytest.mark.parametrize(
    ("missing", "is_toolkit"),
    [
        ("aion.toolkits", True),
        ("aion.toolkits.behavior_evolution", True),
        ("aion.toolkits.behavior_evolution.plugin", True),
        ("pydantic_settings", False),
        ("aion.toolkits.other", False),
        (None, False),
    ],
)
def test_a_missing_module_is_the_toolkit_only_when_it_is_on_the_toolkits_path(missing, is_toolkit) -> None:
    assert evolution._is_toolkit_module(missing) is is_toolkit


def test_a_toolkit_whose_own_import_fails_is_not_taken_for_a_missing_one(monkeypatch) -> None:
    def broken(_name):
        raise ModuleNotFoundError("No module named 'pydantic_settings'", name="pydantic_settings")

    monkeypatch.setattr(evolution.importlib, "import_module", broken)

    with pytest.raises(ModuleNotFoundError):
        executor(provider="local_session")


def test_the_aion_provider_needs_the_deployments_platform_credentials(fake_toolkit, monkeypatch) -> None:
    monkeypatch.setattr(evolution, "api_settings", SimpleNamespace(has_credentials=False))
    with pytest.raises(ConfigurationError, match="AION_CLIENT_ID and AION_CLIENT_SECRET"):
        executor()

    monkeypatch.setattr(evolution, "api_settings", SimpleNamespace(has_credentials=True))
    executor()


def test_the_custom_provider_needs_its_endpoint_and_no_key(fake_toolkit, monkeypatch) -> None:
    monkeypatch.delenv("EVOLUTION_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("EVOLUTION_MODEL_API_KEY", raising=False)
    with pytest.raises(ConfigurationError, match="needs EVOLUTION_MODEL_BASE_URL, which is not set"):
        executor(provider="custom")

    monkeypatch.setenv("EVOLUTION_MODEL_BASE_URL", "http://localhost:11434/v1")
    executor(provider="custom")


def test_the_local_session_provider_needs_nothing_of_the_sdk(fake_toolkit) -> None:
    executor(provider="local_session")


def test_the_toolkit_has_the_last_word_on_whether_it_can_run_here(fake_toolkit, monkeypatch) -> None:
    monkeypatch.setenv(fake_toolkit.UNAVAILABLE_VARIABLE, "the Codex CLI is not installed")

    with pytest.raises(ConfigurationError, match="the Codex CLI is not installed"):
        executor(provider="local_session")


def test_the_toolkit_is_created_with_the_provider(fake_toolkit, monkeypatch) -> None:
    monkeypatch.setenv("EVOLUTION_MODEL_BASE_URL", "https://models.example.test/v1")

    executor(provider="custom")

    [toolkit] = fake_toolkit.EvolutionToolkit.created
    assert toolkit.provider == "custom"


def test_an_option_evolution_does_not_take_stops_the_startup(fake_toolkit) -> None:
    with pytest.raises(ConfigurationError, match="model"):
        executor(model="gpt")


# -- registration -------------------------------------------------------------------


def evolution_config(**options: Any) -> AgentConfig:
    return AgentConfig(path="agent:graph", enabled_extensions=[DAEMON_EXTENSION_URI_V1, {URI: options or None}])


def test_without_the_toolkit_an_enabled_evolution_stops_the_startup(no_toolkit, isolated_registry) -> None:
    enabled = register_extensions(evolution_config(provider="local_session"), registry=isolated_registry)

    with pytest.raises(ConfigurationError, match="not installed"):
        create_extension_executors(enabled)


def test_with_the_toolkit_an_enabled_evolution_serves_with_its_options(fake_toolkit, isolated_registry) -> None:
    enabled = register_extensions(
        evolution_config(callers=["anonymous_session"], provider="local_session"), registry=isolated_registry
    )

    executors = create_extension_executors(enabled)

    assert executors[URI].options == EvolutionOptions(callers=(Caller.ANONYMOUS_SESSION,), provider=Provider.LOCAL_SESSION)


# -- what a new task is refused with -----------------------------------------------------


def caller(credential: CredentialKind) -> AuthenticatedCaller:
    gateway = GatewayCoordinates("identity-1", "environment-1", "terminal-1") if credential is CredentialKind.INVOCATION else None
    return AuthenticatedCaller(
        Principal("ExternalAnonymous", "caller-1"),
        credential=credential,
        assurance=Assurance.SESSION,
        issuer="aion.io",
        claims={},
        gateway=gateway,
    )


class _User(User):
    """The caller's user, as the authentication middleware names it."""

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return "alice"


def call_context(credential: CredentialKind | None = None) -> ServerCallContext:
    state = {"auth": CallerCredentials(caller(credential))} if credential is not None else {}
    return ServerCallContext(user=_User(), state=state)


def new_task() -> Task:
    task = Task(id=TASK_ID, context_id=CONTEXT_ID, status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED))
    task.metadata[ROUTED] = URI
    return task


def request(credential: CredentialKind | None = None, *, script: Any = None, directive: bool = True) -> RequestContext:
    """A request that starts the evolution task ``TASK_ID``; its directive carries ``script``."""
    if directive:
        message = scripted_message(script or {}, uri=URI, text="evolve", task_id=TASK_ID, context_id=CONTEXT_ID)
    else:
        message = scripted_message({}, uri=URI, text="evolve", task_id=TASK_ID, context_id=CONTEXT_ID)
        del message.parts[1:]
    return RequestContext(
        call_context=call_context(credential),
        request=SendMessageRequest(message=message),
        task_id=TASK_ID,
        context_id=CONTEXT_ID,
        task=new_task(),
    )


def config() -> ExecutionConfig:
    return ExecutionConfig(task_id=TASK_ID, context_id=CONTEXT_ID)


async def run(
        evolution: EvolutionExecutor, context: RequestContext, memory: Optional[InMemoryConversations] = None
) -> list[Any]:
    """Run the turn ``context`` starts, over the history of ``memory`` - an empty context by default."""
    memory = memory or InMemoryConversations()
    await memory.hold(CONTEXT_ID, "alice")
    install(memory.for_turn(context.call_context, context.current_task))
    return [event async for event in evolution.stream(context, config())]


async def refusal(
        evolution: EvolutionExecutor, context: RequestContext, memory: Optional[InMemoryConversations] = None
) -> ExtensionRejection:
    with pytest.raises(ExtensionRejection) as error:
        await run(evolution, context, memory)
    return error.value


@pytest.mark.parametrize(
    ("callers", "credential"),
    [
        (("platform",), CredentialKind.SESSION),
        (("platform",), None),
        (("anonymous_session",), CredentialKind.INVOCATION),
        (("platform", "anonymous_session"), None),
    ],
    ids=["session-to-platform-only", "app-user-to-platform-only", "invocation-to-session-only", "app-user-to-both"],
)
async def test_a_caller_the_options_do_not_list_is_refused_before_the_run_starts(
    fake_toolkit, callers, credential
) -> None:
    evolution = executor(callers=list(callers), provider="local_session")

    refused = await refusal(evolution, request(credential))

    assert (refused.code, refused.retryable) == (codes.CALLER_NOT_ALLOWED, False)
    assert ", ".join(callers) in refused.message
    assert fake_toolkit.EvolutionToolkit.created[-1].preflights == []


@pytest.mark.parametrize(
    ("callers", "credential"),
    [
        (("platform",), CredentialKind.INVOCATION),
        (("anonymous_session",), CredentialKind.SESSION),
    ],
)
async def test_a_caller_the_options_list_reaches_the_toolkit(fake_toolkit, callers, credential) -> None:
    evolution = executor(callers=list(callers), provider="local_session")

    await run(evolution, request(credential))

    [handed] = fake_toolkit.EvolutionToolkit.created[-1].preflights
    assert (handed.message_id, handed.context_id, handed.text) == ("m-user", CONTEXT_ID, "evolve")
    assert isinstance(handed.directive, fake_toolkit.EvolutionDirectiveEventPayload)


async def test_a_request_without_a_directive_reaches_the_toolkit_with_none(fake_toolkit) -> None:
    evolution = executor(callers=["anonymous_session"], provider="local_session")

    await run(evolution, request(CredentialKind.SESSION, directive=False))

    [handed] = fake_toolkit.EvolutionToolkit.created[-1].preflights
    assert handed.directive is None


async def test_a_malformed_directive_is_refused_as_an_invalid_payload(fake_toolkit) -> None:
    evolution = executor(callers=["anonymous_session"], provider="local_session")

    refused = await refusal(evolution, request(CredentialKind.SESSION, script={"outputs": "not a list"}))

    assert refused.code == codes.INVALID_PAYLOAD
    assert fake_toolkit.EvolutionToolkit.created[-1].preflights == []


async def test_the_toolkits_refusal_reaches_the_caller(fake_toolkit) -> None:
    evolution = executor(callers=["anonymous_session"], provider="local_session")
    refuse = Refusal(code="forbidden_target", message="Not this repository.").model_dump()

    refused = await refusal(evolution, request(CredentialKind.SESSION, script={"refuse": refuse}))

    assert (refused.code, refused.message) == ("forbidden_target", "Not this repository.")


async def test_the_aion_provider_refuses_a_caller_the_model_service_cannot_serve(fake_toolkit, monkeypatch) -> None:
    monkeypatch.setattr(evolution, "api_settings", SimpleNamespace(has_credentials=True))
    evolution_executor = executor(callers=["anonymous_session"])
    monkeypatch.setattr(model_access, "api_settings", SimpleNamespace(has_credentials=False))

    refused = await refusal(evolution_executor, request(CredentialKind.SESSION))

    assert (refused.code, refused.message) == (codes.CALLER_NOT_ALLOWED, evolution.NO_MODEL_SERVICE)
    assert fake_toolkit.EvolutionToolkit.created[-1].turns == []


# -- one task per context ---------------------------------------------------------------------


async def context_with(*tasks: Task) -> InMemoryConversations:
    """A store holding ``tasks``, each owned by the anonymous caller."""
    memory = InMemoryConversations()
    for stored in tasks:
        await memory.store.save(stored, ServerCallContext())
    return memory


def routed_task(task_id: str, state: TaskState, uri: str = URI, context_id: str = CONTEXT_ID) -> Task:
    built = Task(id=task_id, context_id=context_id, status=TaskStatus(state=state))
    built.metadata[ROUTED] = uri
    return built


async def test_a_second_evolution_in_a_context_is_refused_naming_the_unfinished_one(fake_toolkit) -> None:
    held = await context_with(routed_task("running", TaskState.TASK_STATE_WORKING))
    evolution = executor(callers=["anonymous_session"], provider="local_session")

    refused = await refusal(evolution, request(CredentialKind.SESSION), held)

    assert (refused.code, refused.retryable) == (codes.CONTEXT_BUSY, True)
    assert "running" in refused.message


async def test_a_caller_the_options_do_not_list_learns_nothing_of_the_context(fake_toolkit) -> None:
    held = await context_with(routed_task("running", TaskState.TASK_STATE_WORKING))
    evolution = executor(callers=["platform"], provider="local_session")

    refused = await refusal(evolution, request(CredentialKind.SESSION), held)

    assert refused.code == codes.CALLER_NOT_ALLOWED
    assert "running" not in refused.message


@pytest.mark.parametrize(
    "existing",
    [
        pytest.param(routed_task("done", TaskState.TASK_STATE_CANCELED), id="finished-task"),
        pytest.param(routed_task("other-context", TaskState.TASK_STATE_WORKING, context_id="ctx-2"), id="other-context"),
        pytest.param(routed_task("other", TaskState.TASK_STATE_WORKING, uri="https://x.invalid/t"), id="other-extension"),
        pytest.param(routed_task(TASK_ID, TaskState.TASK_STATE_SUBMITTED), id="the-task-itself"),
    ],
)
async def test_an_evolution_starts_when_the_context_holds_no_other_unfinished_one(fake_toolkit, existing) -> None:
    evolution = executor(callers=["anonymous_session"], provider="local_session")

    await run(evolution, request(CredentialKind.SESSION), await context_with(existing))

    assert len(fake_toolkit.EvolutionToolkit.created[-1].turns) == 1


# -- a run ----------------------------------------------------------------------------


def reported(events: list[Any]) -> dict:
    return MessageToDict(events[0].status.message.parts[0])["data"]


@pytest.fixture
def model_service(monkeypatch):
    """A deployment with platform credentials, whose model service lives at a test URL."""
    monkeypatch.setattr(evolution, "api_settings", SimpleNamespace(has_credentials=True))
    monkeypatch.setattr(model_access, "api_settings", SimpleNamespace(has_credentials=True))
    monkeypatch.setattr(model_access, "aion_model_base_url", lambda: "https://api.example.test/v1")


async def test_a_run_with_the_aion_provider_calls_the_model_service_on_the_callers_behalf(
    fake_toolkit, model_service, runtime_provider
) -> None:
    evolution_executor = executor(callers=["platform"], provider="aion")
    context = request(CredentialKind.INVOCATION, script={"report": True, "outputs": [COMPLETED]})
    runtime_provider.set_current_context(
        AionRuntimeContext(callback_attribution=callback_attribution(context.call_context, AionRuntimeContext()))
    )

    events = await run(evolution_executor, context)

    report = reported(events)
    assert report["provider"] == "aion"
    assert report["model"] == {
        "base_url": "https://api.example.test/v1",
        "attribution": ["Aion-Caller-Id", caller(CredentialKind.INVOCATION).subject],
        "keyed": True,
    }
    assert events[-1].status.state == TaskState.TASK_STATE_COMPLETED
    _kind, _request, run_handed = fake_toolkit.EvolutionToolkit.created[-1].turns[0]
    assert run_handed.model.secret is model_access.aion_jwt_api_key
    assert (run_handed.task_id, run_handed.context_id, run_handed.turn_id) == (TASK_ID, CONTEXT_ID, "m-user")


async def test_a_run_with_the_custom_provider_reads_its_key_at_every_call(fake_toolkit, monkeypatch) -> None:
    monkeypatch.setenv("EVOLUTION_MODEL_BASE_URL", "https://models.example.test/v1")
    monkeypatch.setenv("EVOLUTION_MODEL_API_KEY", "model-key-1")
    evolution_executor = executor(callers=["anonymous_session"], provider="custom")

    events = await run(evolution_executor, request(CredentialKind.SESSION, script={"report": True, "outputs": [COMPLETED]}))

    assert reported(events)["model"] == {"base_url": "https://models.example.test/v1", "attribution": None, "keyed": True}
    _kind, _request, run_handed = fake_toolkit.EvolutionToolkit.created[-1].turns[0]
    assert await run_handed.model.secret() == "model-key-1"
    monkeypatch.setenv("EVOLUTION_MODEL_API_KEY", "model-key-2")
    assert await run_handed.model.secret() == "model-key-2"


async def test_a_run_with_the_custom_provider_without_a_key_calls_its_endpoint_with_none(
    fake_toolkit, monkeypatch
) -> None:
    monkeypatch.setenv("EVOLUTION_MODEL_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.delenv("EVOLUTION_MODEL_API_KEY", raising=False)
    evolution_executor = executor(callers=["anonymous_session"], provider="custom")

    events = await run(evolution_executor, request(CredentialKind.SESSION, script={"report": True, "outputs": [COMPLETED]}))

    assert reported(events)["model"] == {"base_url": "http://localhost:11434/v1", "attribution": None, "keyed": False}


async def test_a_run_with_the_local_session_provider_gets_no_model_endpoint(fake_toolkit, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret_value")
    evolution_executor = executor(callers=["anonymous_session"], provider="local_session")

    events = await run(evolution_executor, request(CredentialKind.SESSION, script={"report": True, "outputs": [COMPLETED]}))

    assert reported(events)["model"] is None
    _kind, _request, run_handed = fake_toolkit.EvolutionToolkit.created[-1].turns[0]
    assert "ghp_secret_value" in run_handed.secret_values


async def test_a_run_gets_the_generation_of_its_context(fake_toolkit) -> None:
    evolution_executor = executor(callers=["anonymous_session"], provider="local_session")

    events = await run(evolution_executor, request(CredentialKind.SESSION, script={"report": True, "outputs": [COMPLETED]}))

    assert reported(events)["context_generation"] is not None


async def test_the_artifacts_a_run_writes_are_numbered_in_the_context(fake_toolkit) -> None:
    memory = InMemoryConversations()
    earlier = routed_task("earlier", TaskState.TASK_STATE_COMPLETED)
    earlier.artifacts.append(Artifact(artifact_id="evolution-spec", parts=[Part(text="v0")]))
    earlier.artifacts[0].metadata["version"] = "0"
    await memory.store.save(earlier, call_context(CredentialKind.SESSION))
    evolution_executor = executor(callers=["anonymous_session"], provider="local_session")
    script = {"outputs": [{"kind": "artifact", "id": "evolution-spec", "text": "v1"}, COMPLETED]}

    events = await run(evolution_executor, request(CredentialKind.SESSION, script=script), memory)

    [written] = [event for event in events if isinstance(event, TaskArtifactUpdateEvent)]
    assert written.artifact.metadata["version"] == "1"


async def test_a_cancel_reaches_the_toolkit_with_how_long_the_server_waits(fake_toolkit) -> None:
    evolution_executor = executor(provider="local_session")

    await evolution_executor.cancel(ExecutionConfig(task_id="t-1"), within=15.0)
    await evolution_executor.cancel(ExecutionConfig(task_id="t-2"))

    assert fake_toolkit.EvolutionToolkit.created[-1].runner.cancels == [("t-1", 15.0), ("t-2", None)]
