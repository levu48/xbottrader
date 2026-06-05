"""Re-export — strategy factory now lives in xbt_core (see app/strategies/base.py).

The bot-engine's pydantic ``StrategyConfig`` duck-types into ``build_strategy``.
"""

from xbt_core.strategies.factory import build_strategy  # noqa: F401
