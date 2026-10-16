"""Which of the server's environment values a toolkit must never report, and how they are masked."""

from __future__ import annotations

from aion.server.agent.execution.extensions.toolkit.secret_values import (
    MASK,
    SecretMasker,
    deployment_secret_values,
)


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


def test_every_occurrence_is_masked_the_longest_value_first() -> None:
    masker = SecretMasker(["secret-value", "secret-value-longer"])

    assert masker.text("a secret-value-longer and a secret-value") == f"a {MASK} and a {MASK}"


def test_a_value_too_short_to_be_a_secret_is_left_alone() -> None:
    masker = SecretMasker(["true", "5432"])

    assert masker.values == ()
    assert masker.text("true on 5432") == "true on 5432"


def test_json_values_are_masked_at_every_depth_and_keys_are_kept() -> None:
    masker = SecretMasker(["secret-value"])

    masked = masker.json({"secret-value": ["x secret-value", {"inner": "secret-value"}, 3, None]})

    assert masked == {"secret-value": [f"x {MASK}", {"inner": MASK}, 3, None]}


def test_a_value_added_later_is_masked_from_then_on() -> None:
    masker = SecretMasker()
    masker.add("minted-token-1")

    assert masker.text("Bearer minted-token-1") == f"Bearer {MASK}"
    assert "minted" not in repr(masker)
