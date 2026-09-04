"""PostgreSQL models, migrations and task repositories for the SDK."""

from aion.core.utils.optional_deps import is_own_module, missing_server_extra_error

# This package ships in every wheel; SQLAlchemy, Alembic and psycopg only
# arrive with a server extra. Without them the import fails deep inside a
# third-party module, on a name that means nothing to whoever wrote the agent.
try:
    from aion.db.postgres.fields import PydanticType, ProtobufType
    from aion.db.postgres.repositories import BaseRepository
    from aion.db.postgres.records import TaskClaimRecord, TaskRecord
    from aion.db.postgres.models import TaskClaimModel, TaskRecordModel
    from aion.db.postgres.utils import convert_pg_url, verify_connection, validate_permissions
    from aion.db.postgres.constants import AION_SCHEMA, TASK_CLAIMS_TABLE, TASKS_TABLE
    from aion.db.postgres.manager import DbManager, db_manager
    from aion.db.postgres.factory import DbFactory
    from aion.db.postgres.migrations import upgrade_to_head
    from aion.db.postgres.types import Pagination
except ModuleNotFoundError as exc:
    if is_own_module(exc.name):
        raise
    raise missing_server_extra_error("aion.db.postgres", exc) from exc


__all__ = [
    "PydanticType", "ProtobufType", "BaseRepository", "TaskClaimRecord", "TaskRecord", "TaskRecordModel",
    "convert_pg_url", "verify_connection", "validate_permissions",
    "AION_SCHEMA", "TASKS_TABLE", "TASK_CLAIMS_TABLE",
    "TaskClaimModel",
    "DbManager", "db_manager", "DbFactory", "upgrade_to_head",
    "Pagination",
]
