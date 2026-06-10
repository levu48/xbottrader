"""Operator-authored Python strategies.

Each module here defines a pure :class:`~xbt_core.strategies.base.Strategy`
subclass and ends with ``register("key")(Cls)``. They are imported for their
registration side effects by ``python_registry._ensure_loaded`` — there is no
need to list them here.
"""
