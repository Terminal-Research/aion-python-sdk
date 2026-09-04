"""Small helpers shared across ``aion.cli``.

A regular package rather than an implicit namespace one: the import graph
behind the layer contract is not descended into directories that have no
``__init__.py``, and this tree would otherwise sit outside the contract.
"""
