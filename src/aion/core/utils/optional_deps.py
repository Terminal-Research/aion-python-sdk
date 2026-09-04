"""Telling a missing optional dependency from a broken installation.

One distribution ships every ``aion.*`` subpackage; extras only add third-party
libraries. So a ``ModuleNotFoundError`` raised while importing part of the SDK
means one of two very different things, and the caller has to say which:

* the missing module is one of ours - the wheel is incomplete, or something
  shadows the ``aion`` namespace. Nothing the user can install fixes it, and
  swallowing it hides a packaging defect.
* the missing module is a third-party one - an extra is not installed. That is
  ordinary, and the fix is one command, which :func:`missing_extra_error`
  writes out.
"""

from __future__ import annotations

# The name on PyPI, not the import namespace: it is what goes after `pip
# install`, and the two do not match.
DISTRIBUTION = "aionto-sdk"


class MissingOptionalDependency(ImportError):
    """An optional extra of this distribution is not installed."""


def is_own_module(name: str | None) -> bool:
    """Is ``name`` a module that this distribution ships?

    Other distributions may share the ``aion`` namespace - the behaviour
    evolution toolkit publishes ``aion.toolkits.*`` - and this answers yes for
    those too. Callers that can encounter one should check for it by name
    before asking; the plugin loader is the only place that comes close, and
    the toolkit is not among the plugins it loads.
    """
    return bool(name) and (name == "aion" or name.startswith("aion."))


def missing_extra_error(
    feature: str,
    extra: str,
    cause: BaseException | None = None,
) -> MissingOptionalDependency:
    """Build the error for ``feature`` needing the ``extra`` install extra.

    Returned rather than raised, so the caller can attach the original import
    failure with ``raise missing_extra_error(...) from exc``.
    """
    return MissingOptionalDependency(
        f"{feature} requires optional dependencies.\n"
        f'Install them with: pip install "{DISTRIBUTION}[{extra}]"',
        name=getattr(cause, "name", None),
    )
