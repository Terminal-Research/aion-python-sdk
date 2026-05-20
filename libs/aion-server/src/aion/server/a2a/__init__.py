from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

try:
    from a2a._base import to_camel_custom
except ImportError:
    def to_camel_custom(snake: str) -> str:
        """Convert a snake_case string to camelCase.

        Args:
            snake: The string to convert.

        Returns:
            The converted camelCase string.
        """
        # First, remove any trailing underscores. This is common for names that
        # conflict with Python keywords, like 'in_' or 'from_'.
        if snake.endswith('_'):
            snake = snake.rstrip('_')
        return to_camel(snake)

try:
    from a2a._base import A2ABaseModel
except ImportError:
    class A2ABaseModel(BaseModel):
        """Base class for shared behavior across A2A data models.

        Provides a common configuration (e.g., alias-based population) and
        serves as the foundation for future extensions or shared utilities.

        This implementation provides backward compatibility for camelCase aliases
        by lazy-loading an alias map upon first use. Accessing or setting
        attributes via their camelCase alias will raise a DeprecationWarning.
        """

        model_config = ConfigDict(
            # SEE: https://docs.pydantic.dev/latest/api/config/#pydantic.config.ConfigDict.populate_by_name
            validate_by_name=True,
            validate_by_alias=True,
            serialize_by_alias=True,
            alias_generator=to_camel_custom,
        )
