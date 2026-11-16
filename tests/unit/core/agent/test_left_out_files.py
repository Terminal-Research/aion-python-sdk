"""Which files of a model request to leave out, found by the tries that fail.

``RefusedFileSearch`` is driven here the way a model class drives it: each
try leaves out the files ``left_out`` names, a stand-in model fails a try
that still carries a file it refuses, and the search is told how each try
ended. What is asserted is which tries are made and what the model is left
with.
"""

from __future__ import annotations

import logging
from typing import Optional

import pytest

from aion.core.a2a.files import StoredFile
from aion.core.agent.left_out_files import (
    RefusedFileSearch,
    RequestFile,
    is_request_refusal,
    left_out_note,
    too_large_note,
)
from aion.core.exceptions import TaskFailedError

PDF, PNG, DAT, CLIP = (RequestFile(key=key, name=key, mime_type="application/test") for key in ("pdf", "png", "dat", "clip"))


class _Refused(Exception):
    """How the model service answers a request that carries a file it does not take."""

    status_code = 400


class _RateLimited(Exception):
    status_code = 429


class _Model:
    """Refuses a request that carries any file of ``refuses``; records what each request carried."""

    def __init__(self, *refuses: RequestFile, failure: type[Exception] = _Refused) -> None:
        self.refuses = {file.key for file in refuses}
        self.failure = failure
        self.requests: list[tuple[str, ...]] = []

    def call(self, files: list[RequestFile], left_out: frozenset[str]) -> Optional[Exception]:
        sent = tuple(file.key for file in files if file.key not in left_out)
        self.requests.append(sent)
        return self.failure() if self.refuses & set(sent) else None


def _run(model: _Model, files: list[RequestFile], *, name: str = "model-a") -> frozenset[str]:
    """Make the request as a model class does; the files the try that went through left out."""
    search = RefusedFileSearch(name, files)
    while True:
        left_out = search.left_out
        failure = model.call(files, left_out)
        if failure is None:
            search.succeeded()
            return left_out
        for suspect in search.failed(failure):
            search.checked(suspect, model.call([suspect], frozenset()))


def test_a_request_the_model_takes_is_made_once() -> None:
    model = _Model()

    assert _run(model, [PDF, PNG]) == frozenset()
    assert model.requests == [("pdf", "png")]


def test_the_one_refused_file_is_left_out_and_the_others_reach_the_model() -> None:
    model = _Model(DAT)

    assert _run(model, [PDF, DAT, PNG]) == {"dat"}
    assert model.requests == [
        ("pdf", "dat", "png"),
        ("pdf", "dat"),  # without the newest file: still refused
        ("pdf", "png"),  # without the next one: goes through, and is the answer
    ]


def test_the_newest_file_is_tried_first() -> None:
    """A file of an earlier message went through before; the newest one is the likely refusal."""
    model = _Model(CLIP)

    assert _run(model, [PDF, CLIP]) == {"clip"}
    assert model.requests == [("pdf", "clip"), ("pdf",)]


def test_two_refused_files_are_found_by_checking_each_alone() -> None:
    """No try without one file goes through; each file is then sent alone, and the one the model takes stays."""
    model = _Model(DAT, CLIP)

    assert _run(model, [PDF, DAT, CLIP]) == {"dat", "clip"}
    assert model.requests == [
        ("pdf", "dat", "clip"),
        ("pdf", "dat"),
        ("pdf", "clip"),
        ("dat", "clip"),
        ("clip",),  # each file alone, newest first
        ("dat",),
        ("pdf",),
        ("pdf",),  # the try with both refused files left out
    ]


def test_a_later_request_to_the_same_model_leaves_the_file_out_at_once() -> None:
    _run(_Model(DAT), [PDF, DAT])
    model = _Model(DAT)

    assert _run(model, [PDF, DAT, PNG]) == {"dat"}
    assert model.requests == [("pdf", "png")]


def test_another_model_is_asked_again() -> None:
    """One model refusing a file says nothing of another."""
    _run(_Model(DAT), [PDF, DAT], name="model-a")
    model = _Model()

    assert _run(model, [PDF, DAT], name="model-b") == frozenset()
    assert model.requests == [("pdf", "dat")]


def test_a_file_the_model_took_is_not_suspected_again() -> None:
    """A later failure with no new file is not about a file: it is raised as it is."""
    _run(_Model(), [PDF])
    model = _Model(PDF)

    with pytest.raises(_Refused):
        _run(model, [PDF])
    assert model.requests == [("pdf",)]


def test_a_failure_that_is_not_a_refusal_is_raised_at_once() -> None:
    model = _Model(DAT, failure=_RateLimited)

    with pytest.raises(_RateLimited):
        _run(model, [PDF, DAT])
    assert model.requests == [("pdf", "dat")]


def test_a_request_that_fails_on_no_file_raises_its_first_failure() -> None:
    """Every try fails - on the request's text, say - while every file goes through alone."""
    first = _Refused("the first failure")
    search = RefusedFileSearch("model-a", [PDF, PNG])
    tries = []

    with pytest.raises(_Refused) as raised:
        failure: Exception = first
        while True:
            tries.append(search.left_out)
            for suspect in search.failed(failure):
                search.checked(suspect, None)
            failure = _Refused()

    assert raised.value is first
    assert tries == [frozenset(), {"png"}, {"pdf"}]


def test_a_check_that_fails_otherwise_is_raised() -> None:
    search = RefusedFileSearch("model-a", [PDF])
    search.failed(_Refused())  # the try without the PDF comes next

    with pytest.raises(_RateLimited):
        for suspect in search.failed(_Refused()):
            search.checked(suspect, _RateLimited())


def test_the_try_after_the_checks_does_not_start_another_search() -> None:
    search = RefusedFileSearch("model-a", [PDF])
    search.failed(_Refused())
    for suspect in search.failed(_Refused()):
        search.checked(suspect, _Refused())
    assert search.left_out == {"pdf"}

    after = _Refused("after the checks")
    with pytest.raises(_Refused) as raised:
        search.failed(after)
    assert raised.value is after


def test_the_refusal_is_logged_once_with_the_file_and_the_model(caplog) -> None:
    stored = StoredFile.create("file-1", "ver-1")
    clip = RequestFile.of(b"clip", name="clip.mp4", mime_type="video/mp4", stored=stored)

    with caplog.at_level(logging.WARNING, logger="aion.core.agent.left_out_files"):
        _run(_Model(clip), [PDF, clip])
        _run(_Model(clip), [PDF, clip])

    [record] = caplog.records
    message = record.getMessage()
    for detail in ("model-a", "'clip.mp4'", "video/mp4", "file file-1 version ver-1", "_Refused"):
        assert detail in message


class TestRefusal:
    """What counts as a model refusing what its request carries."""

    @pytest.mark.parametrize("status", [400, 413, 415, 422])
    def test_a_status_that_refuses_the_request(self, status: int) -> None:
        error = Exception()
        error.status_code = status
        assert is_request_refusal(error)

    @pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 503])
    def test_a_status_that_does_not(self, status: int) -> None:
        error = Exception()
        error.status_code = status
        assert not is_request_refusal(error)

    def test_an_error_building_the_request(self) -> None:
        assert is_request_refusal(ValueError("Block of type video is not supported."))

    def test_an_httpx_status_error(self) -> None:
        import httpx

        request = httpx.Request("POST", "https://models.test/v1/chat/completions")
        error = httpx.HTTPStatusError("refused", request=request, response=httpx.Response(415, request=request))
        assert is_request_refusal(error)

    @pytest.mark.parametrize("error", [TaskFailedError("no model"), TimeoutError(), ConnectionError(), RuntimeError()])
    def test_the_sdks_own_failures_and_others_are_not(self, error: Exception) -> None:
        assert not is_request_refusal(error)


class TestNotes:
    def test_a_refused_file(self) -> None:
        assert left_out_note(RequestFile(key="k", name="c.mp4", mime_type="video/mp4")) == (
            '[File "c.mp4" (video/mp4) was not accepted by the model and was left out]'
        )

    def test_a_file_without_a_name(self) -> None:
        assert left_out_note(RequestFile(key="k", mime_type="video/mp4")) == (
            "[A file (video/mp4) was not accepted by the model and was left out]"
        )

    def test_a_file_too_large(self) -> None:
        assert too_large_note("scan.pdf", "application/pdf", 20) == (
            '[File "scan.pdf" (application/pdf) is larger than the 20 bytes a model request may carry and was left out]'
        )


def test_a_stored_file_is_known_by_its_version_and_any_other_by_its_type_and_content() -> None:
    stored = StoredFile.create("file-1", "ver-1")

    assert RequestFile.of(b"a", stored=stored).key == RequestFile.of(b"b", stored=stored).key == "file:file-1:ver-1"
    assert RequestFile.of(b"a", mime_type="text/plain").key == RequestFile.of("a", mime_type="text/plain").key
    assert RequestFile.of(b"a", mime_type="text/plain").key != RequestFile.of(b"b", mime_type="text/plain").key
    assert RequestFile.of(b"a", mime_type="text/plain").key != RequestFile.of(b"a", mime_type="application/pdf").key


def test_the_same_bytes_with_a_type_the_model_takes_are_not_left_out() -> None:
    """A model refuses a file for the type it is sent with."""
    refused = RequestFile.of(b"%PDF report", name="report", mime_type="application/octet-stream")
    _run(_Model(refused), [refused])
    model = _Model(refused)
    pdf = RequestFile.of(b"%PDF report", name="report", mime_type="application/pdf")

    assert _run(model, [pdf]) == frozenset()
    assert model.requests == [(pdf.key,)]
