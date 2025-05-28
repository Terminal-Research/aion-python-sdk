from typing import NotRequired, TypedDict

from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    AuthenticationError,
    BaseUser,
)
from starlette.requests import HTTPConnection

from langgraph_api.auth.langsmith.client import auth_client
from langgraph_api.auth.studio_user import StudioUser
from langgraph_api.config import (
    LANGSMITH_AUTH_VERIFY_TENANT_ID,
    LANGSMITH_TENANT_ID,
)


class AuthDict(TypedDict):
    organization_id: str
    tenant_id: str
    user_id: NotRequired[str]
    user_email: NotRequired[str]


class LangsmithAuthBackend(AuthenticationBackend):
    async def authenticate(
        self, conn: HTTPConnection
    ) -> tuple[AuthCredentials, BaseUser] | None:
        headers = [
            ("Authorization", conn.headers.get("Authorization")),
            ("X-Tenant-Id", conn.headers.get("x-tenant-id")),
            ("X-Api-Key", conn.headers.get("x-api-key")),
            ("X-Service-Key", conn.headers.get("x-service-key")),
        ]
        if not any(h[1] for h in headers):
            raise AuthenticationError("Missing authentication headers")
        async with auth_client() as auth:
            if not LANGSMITH_AUTH_VERIFY_TENANT_ID and not conn.headers.get(
                "x-api-key"
            ):
                # when LANGSMITH_AUTH_VERIFY_TENANT_ID is false, we allow
                # any valid bearer token to pass through
                # api key auth is always required to match the tenant id
                res = await auth.get(
                    "/auth/verify", headers=[h for h in headers if h[1] is not None]
                )
            else:
                res = await auth.get(
                    "/auth/public", headers=[h for h in headers if h[1] is not None]
                )
            if res.status_code == 401:
                raise AuthenticationError("Invalid token")
            elif res.status_code == 403:
                raise AuthenticationError("Forbidden")
            else:
                res.raise_for_status()
                auth_dict: AuthDict = res.json()

            # If tenant id verification is disabled, the bearer token requests
            # are not required to match the tenant id. Api key requests are
            # always required to match the tenant id.
            if LANGSMITH_AUTH_VERIFY_TENANT_ID or conn.headers.get("x-api-key"):
                if auth_dict["tenant_id"] != LANGSMITH_TENANT_ID:
                    raise AuthenticationError("Invalid tenant ID")

        return AuthCredentials(["authenticated"]), StudioUser(
            auth_dict.get("user_id"), is_authenticated=True
        )
