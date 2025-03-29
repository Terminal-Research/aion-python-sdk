"""
LangGraph API Server Implementation for AION Agent API

This module defines the core server implementation that other agent projects can
use to expose their LangGraph-based agents via an API. It follows a similar pattern
to langgraph_api but is customized for the AION architecture.
"""

# MONKEY PATCH: Patch Starlette to fix an error in the library
import langgraph_api.patch  # noqa: F401,I001
# MONKEY PATCH: Patch langgraph_api to implement server for our own needs
import aion.api.agent.patch.patch

import sys

# WARNING: Keep the import above before other code runs as it
# patches an error in the Starlette library.
import logging
import os
from typing import Dict, Any, Optional, List

# Set required environment defaults for langgraph_api
# DATABASE_URI is required by langgraph_api and has no default
os.environ.setdefault("DATABASE_URI", "sqlite:///langgraph.db")

import jsonschema_rs
import structlog
from contextlib import asynccontextmanager
from langgraph.errors import EmptyInputError, InvalidUpdateError
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from langgraph_api.api.openapi import set_custom_spec

import langgraph_api.config as config
from langgraph_api.api import routes, user_router
from langgraph_api.errors import (
    overloaded_error_handler,
    validation_error_handler,
    value_error_handler,
)
from langgraph_api.lifespan import lifespan
from langgraph_api.middleware.http_logger import AccessLoggerMiddleware
from langgraph_api.middleware.private_network import PrivateNetworkMiddleware
from langgraph_api.utils import SchemaGenerator
from langgraph_storage.retry import OVERLOADED_EXCEPTIONS
from langgraph_sdk.client import configure_loopback_transports
from dotenv import load_dotenv

# For development and debugging
from pydantic import BaseModel
from fastapi import FastAPI

# Configure logging
logging.captureWarnings(True)
logger = structlog.stdlib.get_logger(__name__)

# Load environment variables
load_dotenv()

# Set up middleware for the application
middleware = []

# Add private network middleware if configured
if config.ALLOW_PRIVATE_NETWORK:
    middleware.append(Middleware(PrivateNetworkMiddleware))

# Add CORS and access logging middleware
middleware.extend(
    [
        (
            Middleware(
                CORSMiddleware,
                allow_origins=config.CORS_ALLOW_ORIGINS,
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )
            if config.CORS_CONFIG is None
            else Middleware(
                CORSMiddleware,
                **config.CORS_CONFIG,
            )
        ),
        # Note: We're not including the LicenseValidationMiddleware
        Middleware(AccessLoggerMiddleware, logger=logger),
    ]
)

# Configure exception handlers for the application
exception_handlers = {
    ValueError: value_error_handler,
    InvalidUpdateError: value_error_handler,
    EmptyInputError: value_error_handler,
    jsonschema_rs.ValidationError: validation_error_handler,
} | {exc: overloaded_error_handler for exc in OVERLOADED_EXCEPTIONS}


def update_openapi_spec(app):
    """
    Update the OpenAPI specification for the application.
    
    This function ensures that the OpenAPI spec includes both custom routes
    and the standard LangGraph API routes.
    
    Args:
        app: The application instance whose specification will be updated
    """
    spec = None
    if "fastapi" in sys.modules:
        # It might be a FastAPI application
        if isinstance(user_router, FastAPI):
            spec = app.openapi()

    if spec is None:
        # Generate schema from routes
        schemas = SchemaGenerator(
            {
                "openapi": "3.1.0",
                "info": {"title": "AION Agent API", "version": "0.1.2"},
            }
        )
        spec = schemas.get_schema(routes=app.routes)

    if spec:
        set_custom_spec(spec)


# Initialize the application based on user configuration
if user_router:
    # Merge with custom user routes
    app = user_router
    update_openapi_spec(app)
    for route in routes:
        if route.path in ("/docs", "/openapi.json"):
            # Our handlers for these are inclusive of the custom routes and default API ones
            # Don't let these be shadowed
            app.router.routes.insert(0, route)
        else:
            # Everything else could be shadowed
            app.router.routes.append(route)

    # Merge lifespans
    original_lifespan = app.router.lifespan_context
    if app.router.on_startup or app.router.on_shutdown:
        raise ValueError(
            f"Cannot merge lifespans with on_startup or on_shutdown: {app.router.on_startup} {app.router.on_shutdown}"
        )

    @asynccontextmanager
    async def combined_lifespan(app):
        """Combine user-defined lifespan with the LangGraph API lifespan."""
        async with lifespan(app):
            if original_lifespan:
                async with original_lifespan(app):
                    yield
            else:
                yield

    app.router.lifespan_context = combined_lifespan

    # Merge middleware
    app.user_middleware = (app.user_middleware or []) + middleware
    
    # Merge exception handlers
    for k, v in exception_handlers.items():
        if k not in app.exception_handlers:
            app.exception_handlers[k] = v
        else:
            logger.debug(f"Overriding exception handler for {k}")
    
    # Configure loopback transport
    configure_loopback_transports(app)

else:
    # Create a standard Starlette application
    app = Starlette(
        routes=routes,
        lifespan=lifespan,
        middleware=middleware,
        exception_handlers=exception_handlers,
    )