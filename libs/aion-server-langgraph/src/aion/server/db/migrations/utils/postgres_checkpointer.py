import logging

from aion.server.db import psycopg_url
from ..env import config

logger = logging.getLogger(__name__)


def setup_checkpointer_tables() -> None:
    """Setup LangGraph checkpointer tables in the database.

    This function creates the necessary tables for PostgresSaver checkpointer
    if they don't already exist.
    """
    try:
        logger.debug("Setting up LangGraph checkpointer tables")

        # Get connection URL from Alembic config
        conn_url = psycopg_url(config.get_main_option("sqlalchemy.url"))

        # Import here to avoid issues if langgraph-checkpoint-postgres is not available
        from langgraph.checkpoint.postgres import PostgresSaver

        # Create checkpointer and setup tables
        with PostgresSaver.from_conn_string(psycopg_url(conn_url)) as checkpointer:
            checkpointer.setup()

    except ImportError:
        logger.warning("langgraph-checkpoint-postgres not available, skipping checkpointer setup")
    except Exception as e:
        logger.error(f"Failed to setup checkpointer tables: {e}", exc_info=True)
