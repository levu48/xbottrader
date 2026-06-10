"""Operator-authored Python strategies, selected by key.

The JSON rule-engine (:mod:`xbt_core.strategies.rule_engine`) is logic-as-data so
*untrusted* end-users can author strategies without us running their code. This
module is the complement for the *operator*: a strategy written as ordinary
Python — strictly more expressive — registered under a key and selected by a
``strategy_type="python"`` config. It is safe precisely because only the operator
adds modules here (they ship in the image, deployed normally); nothing is uploaded
or ``eval``-ed at runtime.

A registered strategy MUST honour the same purity contract as every other
:class:`~xbt_core.strategies.base.Strategy`: ``on_bar`` is a pure function of its
inputs — no I/O, no wall-clock, no randomness — so the same bars always produce
the same intents. That contract is what lets the identical class run in both the
live supervisor and the backtester (parity by construction).

Adding a strategy:
    1. Drop a module in ``strategies/python/`` defining a ``Strategy`` subclass.
    2. Give it a ``from_params(params: Mapping) -> Strategy`` classmethod that
       validates/coerces the free-form ``params`` dict (decimals via
       ``Decimal(str(x))``) — the typed boundary for otherwise-opaque params.
    3. End the module with ``register("your_key")(YourStrategy)``.
No schema, factory, or UI changes are needed per strategy after the one-time
``python`` plumbing.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Callable, Mapping, Protocol, runtime_checkable

from .base import Strategy

# A factory turns the opaque params dict into a runtime Strategy. A Strategy
# subclass exposing ``from_params`` satisfies this directly.
PythonStrategyFactory = Callable[[Mapping[str, object]], Strategy]


@runtime_checkable
class _HasFromParams(Protocol):
    def from_params(self, params: Mapping[str, object]) -> Strategy: ...


_REGISTRY: dict[str, PythonStrategyFactory] = {}
_loaded = False


def register(key: str) -> Callable[[object], object]:
    """Register a strategy under ``key``. Use as a decorator.

    The decorated object is either a plain ``Callable[[Mapping], Strategy]`` or a
    ``Strategy`` subclass with a ``from_params`` classmethod (the common case).
    Returns the object unchanged so it can still be imported/tested directly.
    """

    def _decorate(obj: object) -> object:
        if key in _REGISTRY:
            raise ValueError(f"python strategy_key already registered: {key!r}")
        if isinstance(obj, _HasFromParams):
            _REGISTRY[key] = obj.from_params
        elif callable(obj):
            _REGISTRY[key] = obj  # type: ignore[assignment]
        else:
            raise TypeError(f"cannot register {obj!r}: not callable and has no from_params")
        return obj

    return _decorate


def _ensure_loaded() -> None:
    """Import every module in ``strategies/python/`` for its registration side
    effects. Lazy (import-on-first-build) so both the Bot Engine and the AI
    Engine populate the registry through ``build_strategy`` with no per-app
    startup wiring, regardless of import order."""
    global _loaded
    if _loaded:
        return
    from . import python as _pkg

    for mod in pkgutil.iter_modules(_pkg.__path__):
        importlib.import_module(f"{_pkg.__name__}.{mod.name}")
    _loaded = True


def build_python_strategy(key: str, params: Mapping[str, object]) -> Strategy:
    _ensure_loaded()
    factory = _REGISTRY.get(key)
    if factory is None:
        known = ", ".join(sorted(_REGISTRY)) or "<none>"
        raise ValueError(f"unknown python strategy_key: {key!r} (registered: {known})")
    return factory(params)
