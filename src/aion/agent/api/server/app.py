"""
LangGraph API Server for AION Agent API

This module provides a generic LangGraph API server implementation that can be used
to deploy any LangGraph-based agent. Other agent projects can import this module
and use it to expose their agents via an API.
"""

import os
import logging
import json
import pathlib
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from contextlib import contextmanager
from typing import Dict, List, Any, Optional, Callable, Mapping, Sequence, Union

from dotenv import load_dotenv
from fastapi import FastAPI
from langgraph.api import RunRequest, runtime_server_app
from langgraph_api.agent import agent_api_router
from langgraph.graph import StateGraph
from pydantic import BaseModel
import uvicorn

from aion.agent.api.server.config import ServerConfig

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Create FastAPI app
app = FastAPI(
    title="AION Agent API",
    description="LangGraph Server for deploying agent workflows",
    version="0.1.0",
)

# Add the LangGraph API routes
app.include_router(agent_api_router, prefix="/api")


@contextmanager
def patch_environment(**kwargs):
    """
    Temporarily patch environment variables.
    
    Args:
        **kwargs: Environment variables to set during the context.
    """
    original = {}
    try:
        for key, value in kwargs.items():
            if key in os.environ:
                original[key] = os.environ[key]
            if value is not None:
                os.environ[key] = value
            elif key in os.environ:
                del os.environ[key]
        yield
    finally:
        for key in kwargs:
            if key in original:
                os.environ[key] = original[key]
            elif key in os.environ:
                del os.environ[key]


def register_graph(
    graph: Any,
    graph_id: str,
    display_name: str,
    description: str,
) -> None:
    """
    Register a LangGraph with the API server.
    
    Args:
        graph: The compiled LangGraph to register
        graph_id: Unique identifier for the graph
        display_name: Human-readable name for the graph
        description: Description of what the graph does
    """
    runtime_server_app.register_graph(
        graph, 
        graph_id,
        display_name=display_name,
        description=description,
    )
    logger.info(f"Registered graph '{display_name}' with ID: {graph_id}")


@app.get("/")
async def root() -> Dict[str, str]:
    """Root endpoint for the API server."""
    return {
        "message": "AION Agent API Server",
        "status": "running",
        "version": "0.1.0",
    }


def run_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: bool = False,
    graphs: Optional[Dict[str, Any]] = None,
    open_browser: bool = False,
    debug_port: Optional[int] = None,
    wait_for_client: bool = False,
    env: Optional[Union[str, pathlib.Path, Mapping[str, str]]] = None,
    reload_includes: Optional[Sequence[str]] = None,
    reload_excludes: Optional[Sequence[str]] = None,
    **kwargs: Any,
) -> None:
    """
    Run the LangGraph API server.
    
    Args:
        host: Host address to bind to
        port: Port to listen on
        reload: Whether to enable auto-reload on code changes
        graphs: Dictionary mapping graph IDs to LangGraph instances
        open_browser: Whether to open a browser window after the server starts
        debug_port: Port for the debugger to listen on
        wait_for_client: Whether to wait for a debugger client to connect
        env: Environment variables or path to .env file
        reload_includes: Patterns to include for code hot reloading
        reload_excludes: Patterns to exclude from code hot reloading
        **kwargs: Additional arguments to pass to uvicorn.run
    """
    import inspect
    
    start_time = time.time()
    
    # Handle environment variables
    env_vars = env if isinstance(env, Mapping) else None
    if isinstance(env, (str, pathlib.Path)):
        try:
            from dotenv import load_dotenv
            
            load_dotenv(dotenv_path=env)
            env_vars = dict(os.environ)
            logger.debug(f"Loaded environment variables from {env}")
        except ImportError:
            logger.warning(
                "python-dotenv is not installed. Environment variables will not be loaded from file."
            )
    
    # Set up debugging if requested
    if debug_port is not None:
        try:
            import debugpy
        except ImportError:
            logger.warning("debugpy is not installed. Debugging will not be available.")
            logger.info("To enable debugging, install debugpy: pip install debugpy")
            return
        
        debugpy.listen((host, debug_port))
        logger.info(f"🐛 Debugger listening on port {debug_port}. Waiting for client to attach...")
        if wait_for_client:
            debugpy.wait_for_client()
            logger.info("Debugger attached. Starting server...")
    
    local_url = f"http://{host}:{port}"
    
    # Register graphs if provided
    if graphs:
        for graph_id, graph in graphs.items():
            register_graph(
                graph=graph,
                graph_id=graph_id,
                display_name=graph_id.replace("_", " ").title(),
                description=f"Agent graph: {graph_id}",
            )
    
    # Environment patch for configuration
    with patch_environment(**(env_vars or {})):
        # Function to open browser after server starts
        def _open_browser():
            thread_logger = logging.getLogger("browser_opener")
            if not thread_logger.handlers:
                handler = logging.StreamHandler()
                handler.setFormatter(logging.Formatter("%(message)s"))
                thread_logger.addHandler(handler)
            
            while True:
                try:
                    with urllib.request.urlopen(f"{local_url}/") as response:
                        if response.status == 200:
                            thread_logger.info(f"Server started in {time.time() - start_time:.2f}s")
                            thread_logger.info("🌐 Opening API in your browser...")
                            thread_logger.info("URL: " + local_url)
                            webbrowser.open(local_url)
                            return
                except urllib.error.URLError:
                    pass
                time.sleep(0.1)
        
        # Welcome message
        welcome = f"""

        Welcome to

╔═╗╦╔═╗╔╗╔  ╔═╗╔═╗╔═╗╔╗╔╔╦╗  ╔═╗╔═╗╦
╠═╣║║ ║║║║  ╠═╣║ ╦║╣ ║║║ ║   ╠═╣╠═╝║
╩ ╩╩╚═╝╝╚╝  ╩ ╩╚═╝╚═╝╝╚╝ ╩   ╩ ╩╩  ╩

- 🚀 API: \033[36m{local_url}\033[0m
- 📚 API Docs: \033[36m{local_url}/docs\033[0m
- 🖥️ Admin Interface: \033[36m{local_url}/api/admin\033[0m

This server provides endpoints for LangGraph agents.

"""
        logger.info(welcome)
        
        # Open browser if requested
        if open_browser:
            threading.Thread(target=_open_browser, daemon=True).start()
        
        # Filter kwargs to only include those supported by uvicorn.run
        supported_kwargs = {
            k: v
            for k, v in kwargs.items()
            if k in inspect.signature(uvicorn.run).parameters
        }
        
        # Run the server
        uvicorn.run(
            "aion.agent.api.server.app:app",
            host=host,
            port=port,
            reload=reload,
            reload_includes=reload_includes,
            reload_excludes=reload_excludes,
            access_log=False,
            log_config={
                "version": 1,
                "incremental": False,
                "disable_existing_loggers": False,
                "formatters": {
                    "default": {
                        "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    }
                },
                "handlers": {
                    "console": {
                        "class": "logging.StreamHandler",
                        "formatter": "default",
                        "stream": "ext://sys.stdout",
                    }
                },
                "root": {"handlers": ["console"], "level": "INFO"},
            },
            **supported_kwargs,
        )


if __name__ == "__main__":
    run_server()
