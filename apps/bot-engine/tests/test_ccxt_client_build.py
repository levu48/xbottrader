"""build_ccxt_client venue/sandbox routing.

The live client factory must route Alpaca to the real endpoint for the `alpaca`
venue and to the sandbox (paper-api) only for `alpaca-paper`. Crypto venues are
always live. We fake the ccxt class so no network/import is needed.
"""

from __future__ import annotations

from app.exchanges import ccxt_live
from app.exchanges.ccxt_live import ExchangeCredentials, build_ccxt_client

CREDS = ExchangeCredentials(api_key="k", secret="s")


class _FakeClient:
    def __init__(self, config: dict) -> None:
        self.config = config
        self.sandbox: bool | None = None

    def set_sandbox_mode(self, on: bool) -> None:
        self.sandbox = on


def _patch(monkeypatch) -> None:
    monkeypatch.setattr(ccxt_live, "_exchange_class", lambda exchange: _FakeClient)


def test_alpaca_live_uses_real_endpoint(monkeypatch) -> None:
    _patch(monkeypatch)
    client = build_ccxt_client("alpaca", CREDS)
    assert client.sandbox is None  # sandbox never enabled → live api.alpaca.markets


def test_alpaca_paper_uses_sandbox(monkeypatch) -> None:
    _patch(monkeypatch)
    client = build_ccxt_client("alpaca-paper", CREDS)
    assert client.sandbox is True  # → paper-api.alpaca.markets


def test_crypto_venue_is_live(monkeypatch) -> None:
    _patch(monkeypatch)
    client = build_ccxt_client("binance", CREDS)
    assert client.sandbox is None
    assert client.config["apiKey"] == "k"
