"""Re-export of the strategy base from the shared xbt-core package.

The domain moved to ``xbt_core`` so the AI Engine can run the exact same
strategy classes in backtests (parity). This module preserves the original
``app.strategies.base`` import path.
"""

from xbt_core.strategies.base import (  # noqa: F401
    Bar,
    OrderIntent,
    OrderType,
    Side,
    Strategy,
    StrategyState,
)
