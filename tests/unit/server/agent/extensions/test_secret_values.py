"""Which of the server's environment values an executor must never report."""

from __future__ import annotations

from aion.server.agent.execution.extensions.secret_values import deployment_secret_values


def test_a_variable_named_for_a_credential_counts() -> None:
    values = deployment_secret_values(
        {
            "GITHUB_TOKEN": "ghp_value",
            "AION_CLIENT_SECRET": "client-secret",
            "AION_ENCRYPTION_KEY": "encryption-key",
            "SMTP_PASSWORD": "smtp-password",
            "LOGSTASH_HOST": "logstash.internal",
            "AION_CLIENT_ID": "client-id",
        }
    )

    assert values == ["ghp_value", "client-secret", "encryption-key", "smtp-password"]


def test_a_url_counts_for_the_password_it_embeds() -> None:
    values = deployment_secret_values(
        {
            "POSTGRES_URL": "postgresql://aion:p%40ss-word@db.internal:5432/aion",
            "AION_API_HOST": "https://api.example.com",
        }
    )

    assert values == ["p%40ss-word", "p@ss-word"]
