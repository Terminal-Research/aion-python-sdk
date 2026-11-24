"""What the workflow answers, shared by the agent and its scenarios; it imports nothing of ADK.

The scenarios read these without importing the agent, whose ``google.adk.workflow``
an ADK before 2.0 does not have.
"""

from __future__ import annotations

__all__ = ["FIRST_QUESTION", "NAME_QUESTION", "POSTED", "SECOND_QUESTION", "both", "greeting", "noted", "shout", "summary", "written"]

NAME_QUESTION = "What is your name?"
FIRST_QUESTION = "First?"
SECOND_QUESTION = "Second?"
POSTED = "posted from a node"


def shout(text: str) -> str:
    """The ``text`` branch's result for the rest of the message."""
    return text.upper()


def summary(text: str) -> dict:
    """The ``data`` branch's result for the rest of the message."""
    return {"text": text, "words": len(text.split())}


def greeting(name: str) -> str:
    """The ``ask`` branch's result once the user named themselves."""
    return f"Hi {name}"


def both(first: str, second: str) -> str:
    """The ``two`` branch's result once both questions were answered."""
    return f"first={first} second={second}"


def written(text: str) -> str:
    """What the fake model of the agent nodes writes for ``text``."""
    return f"draft of {text}"


def noted(text: str) -> str:
    """What the ``note`` branch posts through the Aion thread before it returns None."""
    return f"noted: {text}"
