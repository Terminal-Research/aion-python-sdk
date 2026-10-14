"""A toolkit plugin the SDK's tests drive the toolkit host with.

``contract`` holds what a client needs to talk to it - its extension URI and
the payload that scripts it - without importing the SDK, so a scenario can
read it. ``toolkit`` is the plugin itself. ``site/`` holds the distribution
metadata of an ``aion.toolkits`` entry point naming it: a directory on
``sys.path`` (``PYTHONPATH`` for a server process) makes the toolkit an
installed one for discovery, without installing anything.
"""
