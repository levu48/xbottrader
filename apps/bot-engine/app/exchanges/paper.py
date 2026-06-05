"""Re-export — paper adapter now lives in xbt_core (see app/exchanges/base.py)."""

from xbt_core.exchanges.paper import (  # noqa: F401
    MarkPriceFn,
    PaperConfig,
    PaperExchangeAdapter,
)
