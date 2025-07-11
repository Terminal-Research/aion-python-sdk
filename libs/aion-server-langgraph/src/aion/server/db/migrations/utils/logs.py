import logging

from ..env import config

logger = logging.getLogger(__name__)


def log_migrations():
    # See what versions Alembic thinks are available
    from alembic import script
    script_directory = script.ScriptDirectory.from_config(config)
    revisions = list(script_directory.walk_revisions())
    logger.debug(f"Available revisions: {[rev.revision for rev in revisions]}")
