"""xbt-core — shared trading domain for xbottrader.

Pure logic (no FastAPI, DB, or network) shared by the Bot Engine and AI Engine:
strategies, the paper-trading fill model, and internal HMAC auth. Keeping one
implementation is what makes backtest results match live execution.
"""
