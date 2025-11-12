from aion.shared.logging import get_logger
from aion.shared.utils.db import psycopg_url

from aion.server.db.migrations.env import config

logger = get_logger()


async def setup_checkpointer_tables() -> None:
    """Setup LangGraph checkpointer tables in the database.

    This function creates the necessary tables for PostgresSaver checkpointer
    if they don't already exist.
    """
    try:
        logger.debug("Setting up LangGraph checkpointer tables")

        # Get connection URL from Alembic config
        conn_url = psycopg_url(config.get_main_option("sqlalchemy.url"))

        # Import here to avoid issues if langgraph-checkpoint-postgres is not available
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

        # Create checkpointer and setup tables
        async with AsyncPostgresSaver.from_conn_string(psycopg_url(conn_url)) as checkpointer:
            await checkpointer.setup()

    except ImportError:
        logger.warning("langgraph-checkpoint-postgres not available, skipping checkpointer setup")
    except Exception as e:
        logger.error(f"Failed to setup checkpointer tables: {e}", exc_info=True)
