"""scripts/release.py, the two-command release flow.

Nothing here runs ``make``, ``git`` or ``gh``: every subprocess is recorded
by a stand-in, so what is tested is the decisions the script makes - what a
version means, in which order the steps run, where a failure stops the run,
and that the release is created last and only after a yes.
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release.py"


def _load_release():
    """Import the script by path: scripts/ is not an importable package."""
    spec = importlib.util.spec_from_file_location("release", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # Registered before executing: a dataclass under `from __future__ import
    # annotations` looks its module up in sys.modules to resolve field types.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


release = _load_release()


# --- versions -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "prerelease"),
    [
        ("0.1.0", False),
        ("1.2.3", False),
        ("0.1.0.post1", False),
        ("0.1.0rc1", True),
        ("0.1.0b2", True),
        ("0.1.0a0", True),
        ("0.2.0.dev3", True),
        ("0.2.0rc1.dev1", True),
        ("2!1.0", False),
    ],
)
def test_prerelease_is_read_off_the_version(text: str, prerelease: bool) -> None:
    version = release.parse_version(text)
    assert version.text == text
    assert version.prerelease is prerelease
    assert version.tag == f"py-v{text}"
    assert version.kind == ("pre-release" if prerelease else "final release")


@pytest.mark.parametrize(
    "text",
    [
        "0.2.0-rc1",  # separator: Poetry would normalise it, the tag would not match
        "0.2.0pr1",  # not a PEP 440 pre-release label at all
        "0.2.0.RC1",  # uppercase, again non-canonical
        "0.2.0+local",  # local versions cannot be uploaded to PyPI
        "01.2.0",  # leading zero
        "v0.2.0",
        "",
        "0.2.0rc",
    ],
)
def test_non_canonical_versions_are_refused(text: str) -> None:
    with pytest.raises(release.ReleaseError, match="canonical PEP 440"):
        release.parse_version(text)


def test_version_is_read_from_the_manifest(tmp_path: Path) -> None:
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text('[project]\nname = "aionto-sdk"\nversion = "0.3.0rc2"\n')
    version = release.read_version(manifest)
    assert version == release.Version(text="0.3.0rc2", prerelease=True)


def test_the_real_manifest_holds_a_releasable_version() -> None:
    """The check `make release` starts with, on the manifest as committed."""
    release.read_version()


# --- recording subprocesses ---------------------------------------------------


class Recorder:
    """A stand-in for ``subprocess.run`` that records argv and scripts exits.

    ``fail_on`` names a substring; the first command whose argv contains it
    exits 1 and every command after it is a test failure, because the script
    must have stopped.
    """

    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.fail_on = fail_on
        self.failed = False

    def __call__(self, argv, **kwargs):  # noqa: ANN001 - subprocess.run's shape
        assert not self.failed, f"ran {argv} after a failing step"
        self.calls.append(list(argv))
        code = 0
        if self.fail_on is not None and any(self.fail_on in part for part in argv):
            self.failed = True
            code = 1
        return subprocess.CompletedProcess(argv, code, stdout="", stderr="")

    def targets(self) -> list[str]:
        """The Makefile targets that were run, in order."""
        return [call[4] for call in self.calls if call[:1] == ["make"]]


GATE_TARGETS = ["check-env", "tests", "lint-imports", "dist-build", "dist-check", "dist-smoke"]


def test_gate_runs_the_make_targets_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()
    monkeypatch.setattr(release.subprocess, "run", recorder)
    release.run_gate(python=None)
    assert recorder.targets() == GATE_TARGETS
    assert all(call[:4] == ["make", "-C", str(release.REPO_ROOT), "--no-print-directory"]
               for call in recorder.calls)
    assert recorder.calls[-1][-1] == "dist-smoke"  # no SMOKE_ARGS unless asked


def test_gate_passes_the_smoke_interpreter_through(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()
    monkeypatch.setattr(release.subprocess, "run", recorder)
    release.run_gate(python="3.12")
    assert recorder.calls[-1][-2:] == ["dist-smoke", "SMOKE_ARGS=--python 3.12"]


def test_gate_stops_at_the_first_failing_step(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(fail_on="lint-imports")
    monkeypatch.setattr(release.subprocess, "run", recorder)
    with pytest.raises(release.ReleaseError, match="step 'layer contract' failed"):
        release.run_gate(python=None)
    assert recorder.targets() == ["check-env", "tests", "lint-imports"]


# --- confirmation -------------------------------------------------------------


VERSION = release.Version(text="0.2.0", prerelease=False)


def test_yes_skips_the_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("prompted despite --yes"))
    release.confirm(VERSION, assume_yes=True)


def test_without_a_terminal_the_prompt_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))  # not a tty, even with a yes queued
    with pytest.raises(release.ReleaseError, match="--yes"):
        release.confirm(VERSION, assume_yes=False)


@pytest.mark.parametrize("answer", ["", "n", "N", "no", "yes please", "  "])
def test_anything_but_yes_stops_the_release(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: answer)
    with pytest.raises(release.ReleaseError, match="not confirmed"):
        release.confirm(VERSION, assume_yes=False)


@pytest.mark.parametrize("answer", ["y", "Y", "yes", " YES "])
def test_yes_in_any_spelling_confirms(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    prompts: list[str] = []
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or answer)
    release.confirm(release.Version(text="0.2.0rc1", prerelease=True), assume_yes=False)
    assert prompts == [
        "\nRelease aionto-sdk 0.2.0rc1 as py-v0.2.0rc1 (pre-release). Are you sure? [y/N] "
    ]


# --- PyPI ---------------------------------------------------------------------


class FakeResponse(io.BytesIO):
    """What ``urlopen`` returns: a readable that is also a context manager."""

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> None:  # noqa: ANN002 - context manager's shape
        self.close()


def test_pypi_reports_a_published_version(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps({"releases": {"0.1.0rc1": [], "0.1.0rc2": []}}).encode()
    monkeypatch.setattr(release.urllib.request, "urlopen", lambda *a, **k: FakeResponse(body))
    assert release.pypi_has_version(release.Version("0.1.0rc2", True)) is True
    assert release.pypi_has_version(release.Version("0.1.0", False)) is False


def test_a_project_with_no_releases_is_not_published(monkeypatch: pytest.MonkeyPatch) -> None:
    def not_found(*args, **kwargs):  # noqa: ANN002, ANN003
        raise urllib.error.HTTPError("https://pypi.org", 404, "Not Found", None, None)

    monkeypatch.setattr(release.urllib.request, "urlopen", not_found)
    assert release.pypi_has_version(VERSION) is False


def test_an_unreachable_pypi_is_an_error_not_a_no(monkeypatch: pytest.MonkeyPatch) -> None:
    def unreachable(*args, **kwargs):  # noqa: ANN002, ANN003
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(release.urllib.request, "urlopen", unreachable)
    with pytest.raises(release.ReleaseError, match="could not reach PyPI"):
        release.pypi_has_version(VERSION)


# --- the commands -------------------------------------------------------------


def test_check_runs_the_gate_and_nothing_else(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()
    monkeypatch.setattr(release.subprocess, "run", recorder)
    monkeypatch.setattr(release, "read_version", lambda: VERSION)
    monkeypatch.setattr(release, "preflight", lambda v: pytest.fail("check ran preflight"))
    assert release.main(["check"]) == 0
    assert recorder.targets() == GATE_TARGETS
    assert all(call[0] == "make" for call in recorder.calls)


def test_publish_runs_preflight_gate_prompt_then_creates_the_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    recorder = Recorder()
    monkeypatch.setattr(release.subprocess, "run", recorder)
    monkeypatch.setattr(release, "read_version", lambda: release.Version("0.2.0rc1", True))
    monkeypatch.setattr(release, "preflight", lambda v: order.append("preflight") or "https://x")
    monkeypatch.setattr(release, "confirm", lambda v, yes: order.append(f"confirm yes={yes}"))

    assert release.main(["publish"]) == 0

    assert order == ["preflight", "confirm yes=False"]
    assert recorder.targets() == GATE_TARGETS
    assert recorder.calls[-1] == [
        "gh", "release", "create", "py-v0.2.0rc1",
        "--target", "main", "--title", "py-v0.2.0rc1", "--generate-notes", "--prerelease",
    ]


def test_a_final_version_is_not_flagged_as_a_prerelease(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder()
    monkeypatch.setattr(release.subprocess, "run", recorder)
    monkeypatch.setattr(release, "read_version", lambda: VERSION)
    monkeypatch.setattr(release, "preflight", lambda v: "https://x")
    monkeypatch.setattr(release, "confirm", lambda v, yes: None)
    assert release.main(["publish", "--yes"]) == 0
    assert recorder.calls[-1][:4] == ["gh", "release", "create", "py-v0.2.0"]
    assert "--prerelease" not in recorder.calls[-1]


def test_a_refused_prompt_creates_nothing(monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # noqa: ANN001
    recorder = Recorder()
    monkeypatch.setattr(release.subprocess, "run", recorder)
    monkeypatch.setattr(release, "read_version", lambda: VERSION)
    monkeypatch.setattr(release, "preflight", lambda v: "https://x")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "n")

    assert release.main(["publish"]) == 1

    assert recorder.targets() == GATE_TARGETS
    assert not any(call[:2] == ["gh", "release"] for call in recorder.calls)
    assert "not confirmed" in capsys.readouterr().err


def test_a_failing_preflight_runs_no_step(monkeypatch: pytest.MonkeyPatch, capsys) -> None:  # noqa: ANN001
    recorder = Recorder()
    monkeypatch.setattr(release.subprocess, "run", recorder)
    monkeypatch.setattr(release, "read_version", lambda: VERSION)

    def dirty(version):  # noqa: ANN001
        raise release.ReleaseError("the working tree has uncommitted changes")

    monkeypatch.setattr(release, "preflight", dirty)
    assert release.main(["publish", "--yes"]) == 1
    assert recorder.calls == []
    assert "uncommitted changes" in capsys.readouterr().err


def test_a_failing_gate_never_reaches_the_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = Recorder(fail_on="dist-check")
    monkeypatch.setattr(release.subprocess, "run", recorder)
    monkeypatch.setattr(release, "read_version", lambda: VERSION)
    monkeypatch.setattr(release, "preflight", lambda v: "https://x")
    monkeypatch.setattr(release, "confirm", lambda v, yes: pytest.fail("prompted after a failure"))
    assert release.main(["publish"]) == 1
    assert recorder.targets() == GATE_TARGETS[:5]


def test_an_unparseable_manifest_version_stops_before_anything_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys  # noqa: ANN001
) -> None:
    manifest = tmp_path / "pyproject.toml"
    manifest.write_text('[project]\nversion = "0.2.0-rc1"\n')
    recorder = Recorder()
    monkeypatch.setattr(release.subprocess, "run", recorder)
    read_version = release.read_version
    monkeypatch.setattr(release, "read_version", lambda: read_version(manifest))

    assert release.main(["check"]) == 1
    assert recorder.calls == []
    assert "canonical PEP 440" in capsys.readouterr().err
