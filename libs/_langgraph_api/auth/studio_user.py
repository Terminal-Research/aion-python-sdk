from langgraph_sdk.auth.types import StudioUser as StudioUserBase
from starlette.authentication import BaseUser


class StudioUser(StudioUserBase, BaseUser):
    """StudioUser class."""
