"""
LangGraph API Server Runner for AION Agent API

This module provides utilities for configuring and launching the LangGraph API server.
It handles environment setup, debugging configuration, and server execution.
"""

import os
import json
import logging
import pathlib
import threading
import time
import webbrowser
from contextlib import contextmanager
from typing import Dict, Any, Optional, Mapping, Sequence, Union

import uvicorn

from aion.api.agent.server.config import ServerConfig

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


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



def _load_env_vars(env: Union[str, pathlib.Path, Mapping, None]) -> Dict[str, str]:
    """
    Load environment variables from various sources.
    
    This function handles loading environment variables from:
    1. A specified .env file path
    2. A mapping of environment variables
    3. The .env file in the current directory
    4. The .env.template file in the package directory
    
    Args:
        env: Source of environment variables, which can be:
            - A string or Path pointing to a .env file
            - A mapping of environment variable key-value pairs
            - None to check for .env files in standard locations
    
    Returns:
        Dictionary of environment variables
    """
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
    else:
        # If no specific .env file was provided, try to load from .env or .env.template
        try:
            from dotenv import load_dotenv
            
            # First try .env in current directory
            if os.path.exists(".env"):
                load_dotenv()
                env_vars = dict(os.environ)
                logger.debug("Loaded environment variables from .env in current directory")
            # Then try the template file in the package directory
            elif os.path.exists(os.path.join(os.path.dirname(__file__), "../../../../../.env.template")):
                template_path = os.path.join(os.path.dirname(__file__), "../../../../../.env.template")
                load_dotenv(dotenv_path=template_path)
                env_vars = dict(os.environ)
                logger.debug(f"Loaded environment variables from template at {template_path}")
        except ImportError:
            logger.warning(
                "python-dotenv is not installed. Environment variables will not be loaded from file."
            )
    
    return env_vars or {}

def _production_vars():
    return {
        LANGGRAPH_AUTH_TYPE:"aion",
        LANGGRAPH_AUTH:{"path":"aion.agent.server.AionAuthBackend", "openapi" : { }}, # see langgraph_api.openapi.LANGGRAPH_AUTH["openapi"] for expected properties 
    }


def run_server(
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: bool = False,
    graphs: Optional[Dict[str, Any]] = None,
    n_jobs_per_worker: int | None = None,
    open_browser: bool = False,
    debug_port: Optional[int] = None,
    wait_for_client: bool = False,
    env: Optional[Union[str, pathlib.Path, Mapping[str, str]]] = None,
    store: Optional[Dict[str, Any]] = None,
    auth: Optional[Dict[str, Any]] = None,
    http: Optional[Dict[str, Any]] = None,
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
        n_jobs_per_worker: Number of jobs per worker
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
    env_vars = _load_env_vars(env)
    
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
    
    # Graph registration is now handled by the LangGraph API
    # via the langgraph.json configuration file
    if graphs:
        logger.warning(
            "Direct graph registration is no longer supported. "
            "Please define your graphs in langgraph.json instead."
        )
    
    # Environment patch for configuration
    with patch_environment(
        MIGRATIONS_PATH="__inmem",
        DATABASE_URI=":memory:",
        REDIS_URI="fake",
        N_JOBS_PER_WORKER=str(n_jobs_per_worker if n_jobs_per_worker else 1),
        LANGGRAPH_STORE=json.dumps(store) if store else None,
        LANGSERVE_GRAPHS=json.dumps(graphs) if graphs else None,
        LANGSMITH_LANGGRAPH_API_VARIANT="local_dev", # they change this to "local" if LANGSMITH_API_KEY
        # LANGSMITH_LANGGRAPH_API_VARIANT="self_hosted", 
        LANGGRAPH_AUTH=json.dumps(auth) if auth else None, 
        LANGGRAPH_HTTP=json.dumps(http) if http else None,
        # See https://developer.chrome.com/blog/private-network-access-update-2024-03
        ALLOW_PRIVATE_NETWORK="true",
        # Include dev or prod-specific variables
        **(_production_vars if False else {}), # @todo
        **(env_vars or {}),
    ):
        # Function to open browser after server starts
        def _open_browser():
            thread_logger = logging.getLogger("browser_opener")
            if not thread_logger.handlers:
                handler = logging.StreamHandler()
                handler.setFormatter(logging.Formatter("%(message)s"))
                thread_logger.addHandler(handler)
            
            while True:
                try:
                    # Simple check to see if server is up
                    import urllib.request
                    import urllib.error
                    
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
            "aion.api.agent.server.server:app",  # Updated import path to point to server.py
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
                    "simple": {
                        "class": "langgraph_api.logging.Formatter",
                    }
                },
                "handlers": {
                    "console": {
                        "class": "logging.StreamHandler",
                        "formatter": "simple",
                        "stream": "ext://sys.stdout",
                    }
                },
                "root": {"handlers": ["console"], "level": "INFO"},
            },
            **supported_kwargs,
        )


if __name__ == "__main__":
    run_server()
