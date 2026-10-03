"""The canonical subject ``aion:v1:<PrincipalType>:<encoded-id>``: one encoder, one decoder, exact round trips."""

import json
from pathlib import Path

import pytest

from aion.core.principal import InvalidPrincipalError, Principal


@pytest.mark.parametrize("vector", json.loads(
    (Path(__file__).parent / "fixtures" / "caller-id-vectors.json").read_text()
))
def test_backend_golden_caller_ids(vector) -> None:
    """Literal Scala wire vectors prevent a self-consistent but divergent codec."""
    principal = Principal(vector["kind"], vector["id"])
    assert principal.subject == vector["subject"]
    assert Principal.from_subject(vector["subject"]) == principal

SESSION_ID = "f9d7daea-df95-4111-8533-5d6f043edaf9"
IDENTITY_ID = "3c9a7e51-2b8d-4f6a-9c0e-1d2f3a4b5c6d"
SENDER_ID = "v1:slack:VDE:VTI"
"""Slack, tenant ``T1``, sender ``U2``: each opaque part unpadded base64url."""


@pytest.mark.parametrize(
    ("principal", "subject"),
    [
        (Principal("AionUser", "alice"), "aion:v1:AionUser:YWxpY2U"),
        (Principal("AnonymousSession", SESSION_ID), "aion:v1:AnonymousSession:ZjlkN2RhZWEtZGY5NS00MTExLTg1MzMtNWQ2ZjA0M2VkYWY5"),
        (Principal("ExternalSender", SENDER_ID), "aion:v1:ExternalSender:djE6c2xhY2s6VkRFOlZUSQ"),
        (Principal("ExternalIdentity", IDENTITY_ID),
         "aion:v1:ExternalIdentity:M2M5YTdlNTEtMmI4ZC00ZjZhLTljMGUtMWQyZjNhNGI1YzZk"),
        (Principal("AionUser", "ü?>"), "aion:v1:AionUser:w7w_Pg"),
    ],
    ids=["plain", "session", "external-sender", "external-identity", "utf8-and-url-alphabet"],
)
def test_a_principal_and_its_subject_round_trip(principal, subject) -> None:
    assert principal.subject == subject
    assert Principal.from_subject(subject) == principal


def test_the_same_id_under_two_types_are_two_subjects() -> None:
    assert Principal("AionUser", IDENTITY_ID).subject != Principal("ExternalIdentity", IDENTITY_ID).subject


@pytest.mark.parametrize("principal_type", ["AionUser", "AionAgentIdentity", "SomeSystemKind"])
def test_an_id_of_a_type_without_a_known_format_is_opaque_and_keeps_its_case(principal_type) -> None:
    """Agent and system types and their formats are Aion's backend contract; nothing more is assumed."""
    assert Principal.from_subject(Principal(principal_type, "AbC").subject).id == "AbC"


def test_an_external_senders_parts_keep_their_case() -> None:
    sender = "v1:telegram:" + Principal("X", "Bot42").subject.rsplit(":", 1)[1] + ":" + Principal(
        "X", "UserAbC"
    ).subject.rsplit(":", 1)[1]

    assert Principal.from_subject(Principal("ExternalSender", sender).subject).id == sender


@pytest.mark.parametrize(
    "sender",
    ["AbC", "v2:slack:VDE:VTI", "v1:slack:VDE", "v1:slack:VDE:VTI:VTI", "v1:Slack:VDE:VTI", "v1::VDE:VTI",
     "v1:sl ack:VDE:VTI", "v1:slack::VTI", "v1:slack:VDE:", "v1:slack:VDF:VTI", "v1:slack:VDE=:VTI",
     "v1:slack:VD+:VTI"],
    ids=["not-composite", "other-version", "no-sender", "extra-part", "uppercase-provider", "no-provider",
         "space-in-provider", "empty-tenant", "empty-sender", "non-canonical-tenant", "padded-tenant",
         "standard-alphabet-tenant"],
)
def test_an_external_sender_id_off_its_format_is_refused(sender) -> None:
    with pytest.raises(InvalidPrincipalError, match="external sender"):
        Principal("ExternalSender", sender)


@pytest.mark.parametrize("identity", ["x", IDENTITY_ID.upper(), "{" + IDENTITY_ID + "}"])
def test_an_external_identity_id_is_a_lowercase_uuid(identity) -> None:
    with pytest.raises(InvalidPrincipalError, match="external identity"):
        Principal("ExternalIdentity", identity)


@pytest.mark.parametrize(
    "subject",
    [
        "aion:v2:AionUser:YWxpY2U",
        "aion:AionUser:YWxpY2U",
        "aion:v1:AionUser",
        "aion:v1:AionUser:",
        "aion:v1::YWxpY2U",
        "aion:v1:aionUser:YWxpY2U",
        "aion:v1:AionUser:YWxpY2U=",
        "aion:v1:AionUser:YWxpY2+",
        "aion:v1:AionUser:YWxpY2V",
        "aion:v1:AionUser:_w",
        "aion:v1:AnonymousSession:YWxpY2U",
        "aion:v1:AnonymousSession:" + Principal("X", SESSION_ID.upper()).subject.rsplit(":", 1)[1],
    ],
    ids=[
        "other-version", "no-version", "no-id", "empty-id", "empty-type", "lowercase-type", "padded",
        "standard-alphabet", "non-canonical-bits", "not-utf8", "session-not-a-uuid", "session-uppercase-uuid",
    ],
)
def test_a_subject_that_is_not_canonical_is_refused(subject) -> None:
    with pytest.raises(InvalidPrincipalError):
        Principal.from_subject(subject)


def test_a_principal_id_is_at_most_1024_bytes() -> None:
    Principal("AionUser", "é" * 512)

    with pytest.raises(InvalidPrincipalError, match="1024"):
        Principal("AionUser", "é" * 512 + "x")
