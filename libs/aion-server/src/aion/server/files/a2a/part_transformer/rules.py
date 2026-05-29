"""Skip rules for A2A part transformation."""

from a2a.types import Part
from abc import ABC, abstractmethod


class PartSkipRule(ABC):
    """Base interface for part skip rules."""

    @abstractmethod
    def should_skip(self, part: Part) -> bool:
        """Check if a part should be skipped during transformation.

        Args:
            part: The A2A Part to check.

        Returns:
            True if the part should be skipped, False otherwise.
        """
        ...


class CompositePartSkipRule(PartSkipRule):
    """Composite rule that applies multiple skip rules.

    A part is skipped if ANY of the rules matches (OR logic).
    """

    def __init__(self, *rules: PartSkipRule) -> None:
        """Initialize with one or more skip rules.

        Args:
            *rules: Variable number of PartSkipRule instances.
        """
        self.rules = rules

    def should_skip(self, part: Part) -> bool:
        """Skip if any rule matches."""
        return any(rule.should_skip(part) for rule in self.rules)


class CardPartSkipRule(PartSkipRule):
    """Skip JSX Card parts — they are UI documents, not files to persist."""

    _CARD_MIME_TYPE = "application/vnd.aion.card+jsx"

    def should_skip(self, part: Part) -> bool:
        """Skip parts with card MIME type."""
        return part.media_type == self._CARD_MIME_TYPE


def create_default_skip_rules() -> CompositePartSkipRule:
    """Create the default set of skip rules.

    Returns:
        A composite rule with all default skip rules (JSX Cards, etc).
    """
    return CompositePartSkipRule(
        CardPartSkipRule(),
    )
