"""The factory has to build a real AionInvocationContext.

AionInvocationContext is a pydantic model whose field type is the
AionRuntimeContext dataclass, so pydantic needs every annotation on that
dataclass to resolve at import time. models.py once imported
AionRuntimeExtensions under TYPE_CHECKING only, which left the model
"not fully defined" and failed every ADK request at the first
factory.create(). Nothing else in the suite constructs the model, so this
is what would catch that again.
"""

from aion.adk.authoring.invocation import AionInvocationContext
from aion.adk.server.invocation.invocation_context import AionInvocationContextFactory
from aion.core.runtime.context.models import AionRuntimeContext
from google.adk.agents import BaseAgent
from google.adk.sessions import InMemorySessionService
from google.genai import types


async def _factory_and_session() -> tuple[AionInvocationContextFactory, object]:
    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name="showcase", user_id="user")
    factory = AionInvocationContextFactory(
        agent=BaseAgent(name="showcase"),
        session_service=session_service,
    )
    return factory, session


async def test_create_builds_the_context_for_an_invocation():
    factory, session = await _factory_and_session()

    context = factory.create(session=session, user_content=types.Content(parts=[types.Part(text="help")]))

    assert isinstance(context, AionInvocationContext)
    assert context.session is session
    assert context.user_content.parts[0].text == "help"


async def test_model_accepts_a_runtime_context():
    factory, session = await _factory_and_session()
    runtime_context = AionRuntimeContext()

    context = AionInvocationContext(
        invocation_id="inv",
        session_service=factory._session_service,
        agent=factory._agent,
        session=session,
        aion_runtime_context=runtime_context,
    )

    assert context.aion_runtime_context is runtime_context
    assert context.aion_runtime_context.is_extension_active() is True
