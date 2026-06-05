"""Re-export — exchange adapter interface now lives in xbt_core.

Preserves the ``app.exchanges.base`` import path; concrete I/O adapters
(ccxt_adapter, ccxt_live) stay in this app.
"""

from xbt_core.exchanges.base import (  # noqa: F401
    ExchangeAdapter,
    FillEvent,
    PlacementResult,
    SubmittedOrder,
    new_order_id,
    utcnow,
)
