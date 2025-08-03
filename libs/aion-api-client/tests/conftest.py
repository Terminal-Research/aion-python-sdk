import warnings
import pytest

# Suppress deprecation warnings emitted by third-party dependencies which are
# not relevant to the behaviour under test.
warnings.filterwarnings("ignore", category=DeprecationWarning)


@pytest.fixture
def anyio_backend():
    """Restrict AnyIO tests to the asyncio backend to avoid trio dependency."""
    return "asyncio"
