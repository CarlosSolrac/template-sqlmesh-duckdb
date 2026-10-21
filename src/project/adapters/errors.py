"""Errors raised by ingestion adapters.

They live beside the adapters, not in `ports.py`, because an adapter may import only siblings (the package imports itself relatively, and
Ruff bans parent-relative imports), and `run()` imports them from here.
"""


class IngestionError(Exception):
    """A raw source could not be loaded; the run stops before anything is planned and the warehouse keeps its previous raw table."""
