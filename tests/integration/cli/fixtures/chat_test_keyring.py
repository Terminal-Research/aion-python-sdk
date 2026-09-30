"""Test-only keyring backend shared by independent credential-helper processes."""

import json
import os
from pathlib import Path

from keyring.backend import KeyringBackend


class Keyring(KeyringBackend):
    """Persist synthetic credentials under pytest's disposable directory only."""

    priority = 1

    def get_password(self, service, username):
        path = Path(os.environ["AION_CHAT_TEST_KEYCHAIN"])
        values = json.loads(path.read_text()) if path.exists() else {}
        return values.get(service, {}).get(username)

    def set_password(self, service, username, password):
        path = Path(os.environ["AION_CHAT_TEST_KEYCHAIN"])
        values = json.loads(path.read_text()) if path.exists() else {}
        values.setdefault(service, {})[username] = password
        path.write_text(json.dumps(values))

    def delete_password(self, service, username):
        path = Path(os.environ["AION_CHAT_TEST_KEYCHAIN"])
        values = json.loads(path.read_text()) if path.exists() else {}
        values.get(service, {}).pop(username, None)
        path.write_text(json.dumps(values))
