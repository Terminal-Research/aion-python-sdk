from aion.core.a2a.enums import A2AMetadataKey
from aion.core.a2a.metadata import is_platform_metadata_key


class TestRoutedExtensionMetadataKey:
    def test_is_platform_owned(self):
        """Must stay in a platform metadata namespace so A2ATaskDeduplicator
        protects it from being overwritten by incoming task patches.
        """
        assert is_platform_metadata_key(A2AMetadataKey.ROUTED_EXTENSION.value)
