"""The canonical subject ``aion:v1:<PrincipalType>:<encoded-id>``: one encoder, one decoder, exact round trips."""

import pytest

from aion.server.auth import InvalidPrincipalError, Principal

SESSION_ID = "f9d7daea-df95-4111-8533-5d6f043edaf9"


@pytest.mark.parametrize(
    ("principal", "subject"),
    [
        (Principal("AionUser", "alice"), "aion:v1:AionUser:YWxpY2U"),
        (Principal("AnonymousSession", SESSION_ID), "aion:v1:AnonymousSession:ZjlkN2RhZWEtZGY5NS00MTExLTg1MzMtNWQ2ZjA0M2VkYWY5"),
        (Principal("ExternalSender", "v1:slack:VDE:VTI"), "aion:v1:ExternalSender:djE6c2xhY2s6VkRFOlZUSQ"),
        (Principal("AionUser", "ü?>"), "aion:v1:AionUser:w7w_Pg"),
    ],
    ids=["plain", "session", "delimiters-in-the-id", "utf8-and-url-alphabet"],
)
def test_a_principal_and_its_subject_round_trip(principal, subject) -> None:
    assert principal.subject == subject
    assert Principal.from_subject(subject) == principal


def test_the_same_id_under_two_types_are_two_subjects() -> None:
    assert Principal("AionUser", "x").subject != Principal("ExternalIdentity", "x").subject


def test_an_opaque_id_keeps_its_case() -> None:
    assert Principal.from_subject(Principal("ExternalSender", "AbC").subject).id == "AbC"


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
