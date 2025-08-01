from sqlalchemy import TypeDecorator, JSON


class JSONType(TypeDecorator):
    """JSON type that handles serialization of custom objects."""

    impl = JSON
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """Process value before binding to database."""
        if value is not None:
            if isinstance(value, list):
                return [item.dict() if hasattr(item, 'dict') else item for item in value]
            elif hasattr(value, 'dict'):
                return value.dict()
        return value

    def process_result_value(self, value, dialect):
        """Process value when retrieving from database."""
        return value
