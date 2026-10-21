"""An executable extension the SDK's tests drive the server with, and a stand-in evolution toolkit built on it.

``contract`` holds what a client needs to talk to it - its extension URI and
the payload that scripts it - without importing the SDK, so a scenario can
read it. ``script`` is what a turn does: the A2A events it yields, a refusal,
a crash, a wait for ``cancel()``. ``executor`` is the scripted
``ExtensionExecutor`` a unit test registers or drives directly.
``fake_toolkit`` is a directory holding a stand-in for the evolution
toolkit's module (``aion.toolkits.behavior_evolution.plugin``), scripted the
same way: on ``sys.path`` (``PYTHONPATH`` for a server process) it makes the
toolkit an installed one for the SDK's evolution wrapper.
"""
