"""
Middleware for fixing Swagger UI OpenAPI paths in AION proxy server.

This middleware ensures that when accessing agent documentation through the proxy
(e.g., /agents/{agent_id}/docs), the Swagger UI loads the correct OpenAPI schema
from /agents/{agent_id}/openapi.json instead of /openapi.json.
"""

import json
import re
from typing import Callable

from aion.shared.logging import get_logger
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response, HTMLResponse, JSONResponse

from ..constants import AGENT_PATH_PATTERN, build_agent_path

logger = get_logger()


class ProxySwaggerUIFixMiddleware(BaseHTTPMiddleware):
    """
    Middleware to fix Swagger UI paths for proxied agents.

    When proxying requests to agents, this middleware:
    1. Modifies /docs HTML to load OpenAPI schema from correct proxy path
    2. Modifies /openapi.json to include correct server URLs
    """

    async def dispatch(
            self,
            request: Request,
            call_next: Callable
    ) -> Response:
        """
        Process request and fix Swagger UI paths if needed.

        Args:
            request: Incoming HTTP request
            call_next: Next middleware/handler in chain

        Returns:
            Response with fixed paths if applicable
        """
        # Check if this is a proxied agent request
        match = AGENT_PATH_PATTERN.match(request.url.path)
        if not match:
            return await call_next(request)

        agent_id = match.group(1)
        agent_path = match.group(2).rstrip('/')
        response = await call_next(request)

        # Fix paths based on endpoint type
        if agent_path == 'docs' or agent_path.endswith('/docs'):
            return await self._fix_docs_html(response, agent_id)

        if agent_path == 'openapi.json' or agent_path.endswith('/openapi.json'):
            return await self._fix_openapi_schema(response, agent_id)

        return response

    @staticmethod
    async def _read_response_body(response: Response) -> bytes:
        """Read full response body from iterator."""
        body = b''
        async for chunk in response.body_iterator:
            body += chunk
        return body

    @staticmethod
    def _create_error_response(body: bytes, response: Response) -> Response:
        """Create error response preserving original content."""
        headers = dict(response.headers)
        headers.pop('content-length', None)
        headers.pop('transfer-encoding', None)
        return Response(
            content=body,
            status_code=response.status_code,
            headers=headers
        )

    async def _fix_docs_html(
            self,
            response: Response,
            agent_id: str
    ) -> Response:
        """
        Fix Swagger UI HTML to use correct OpenAPI URL.

        Replaces relative OpenAPI URL paths with full proxy paths.

        Args:
            response: Response from proxied agent
            agent_id: Agent identifier

        Returns:
            Modified HTML response with corrected OpenAPI URL
        """
        if response.status_code != 200 or 'text/html' not in response.headers.get('content-type', ''):
            return response

        body = b''
        try:
            body = await self._read_response_body(response)
            html = body.decode('utf-8')
            openapi_url = build_agent_path(agent_id, 'openapi.json')

            # Replace OpenAPI URL references
            patterns = [
                (r'url:\s*["\']\/openapi\.json["\']', f'url: "{openapi_url}"'),
                (r'"openapi_url":\s*["\']\/openapi\.json["\']', f'"openapi_url": "{openapi_url}"'),
                (r'https?://[^/"\s]+/openapi\.json', openapi_url),
            ]

            for pattern, replacement in patterns:
                html = re.sub(pattern, replacement, html)

            logger.debug(f"Fixed Swagger UI HTML for agent '{agent_id}': OpenAPI URL set to {openapi_url}")

            modified_content = html.encode('utf-8')
            return HTMLResponse(
                content=modified_content,
                status_code=response.status_code,
                headers={
                    'content-type': 'text/html; charset=utf-8',
                    'content-length': str(len(modified_content))
                }
            )

        except Exception as e:
            logger.error(f"Failed to fix Swagger UI HTML for agent '{agent_id}': {e}", exc_info=True)
            return self._create_error_response(body, response)

    async def _fix_openapi_schema(
            self,
            response: Response,
            agent_id: str
    ) -> Response:
        """
        Fix OpenAPI schema servers section.

        Replaces the servers section with correct proxy prefix.

        Args:
            response: Response from proxied agent
            agent_id: Agent identifier

        Returns:
            Modified JSON response with corrected server URLs
        """
        if response.status_code != 200 or 'application/json' not in response.headers.get('content-type', ''):
            return response

        body = b''
        try:
            body = await self._read_response_body(response)
            schema = json.loads(body)
            agent_prefix = build_agent_path(agent_id)

            schema['servers'] = [
                {
                    'url': agent_prefix,
                    'description': f'Agent: {agent_id} (via AION Proxy)'
                }
            ]

            logger.debug(f"Fixed OpenAPI schema for agent '{agent_id}': Server URL set to {agent_prefix}")

            return JSONResponse(content=schema, status_code=response.status_code)

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse OpenAPI schema for agent '{agent_id}': {e}", exc_info=True)
            return self._create_error_response(body, response)
        except Exception as e:
            logger.error(f"Failed to fix OpenAPI schema for agent '{agent_id}': {e}", exc_info=True)
            return self._create_error_response(body, response)
