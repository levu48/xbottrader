# Plan: Add stock/equity trading (Alpaca) to xbottrader

## Context

xbottrader today is crypto-only. The goal is to add **US-equity trading via Alpaca**,
starting with **paper mode** (real equity market data, simulated fills), with real
order placement wired but gated behind the existing `XBT_ALLOW_LIVE` flag. Fractional
shares are supported, and we want **robust market-hours handling** (sessions/calendar,
no stale-mark circuit-breaker bugs, graceful off-hours behavior).

Why this is tractable: the architecture is already venue-agnostic where it counts — the
`ExchangeAdapter` Protocol, the `OrderRouter`, the `BarSource` Protocol, the injectable
launcher factories, and the pure (asset-class-neutral) strategy domain. Two findings
shrink the work further:

1. **ccxt already ships an `alpaca` module** (verified: ccxt 4.5.56). It has a `test`
   URL → `paper-api.alpaca.markets` (so `set_sandbox_mode(True)` works), supports
   `createOrder`/`cancelOrder`/`fetchOHLCV`/`fetchOpenOrders`/`fetchOrder`, and uses the
   **same ccxt timeframes** (`1m`/`1h`/`1d`) we already use. So we reuse
   `CcxtExchangeAdapter`, `CcxtBarSource`, `build_ccxt_client`,
   `build_public_ccxt_client`, and `build_ccxt_data_client` — **no new SDK, no new
   adapter, no new bar source, no timeframe mapping.**
2. The only genuinely new subsystem is a **market-session/calendar layer**, threaded
   into exactly two places (the circuit breaker and order placement). Everything else is
   schema/config/copy.

The crypto path must stay byte-for-byte unchanged: every new behavior keys off a
venue→asset-class resolution that returns "always open" / "no gating" for crypto.

### Decisions (confirmed with user)
- Broker: **Alpaca**, via the **ccxt alpaca module** (additive; not a separate SDK).
- Scope: **paper-first** — `PaperExchangeAdapter` (simulated fills) over **real Alpaca
  equity bars**; real Alpaca order placement is wired but gated by `XBT_ALLOW_LIVE`, and
  defaults to Alpaca's sandbox/paper endpoint when first enabled.
- Shares: **fractional** (keep `qty = quote/price`; round to venue precision in adapter).
- Market hours: **robust** — calendar/session service + circuit-breaker safety +
  graceful off-hours order rejection + an IDLE state.

---

## Architecture of the change

```
venue "alpaca"  ──► session_for(venue) ──► UsEquitySession (9:30–16:00 ET, holidays)
                                              │  crypto venues ──► CryptoSession (always open)
                                              ▼
ProductionLauncher
  • data feed  : CcxtBarSource over ccxt.alpaca (real equity prices; needs user key)
  • order path : paper mode → PaperExchangeAdapter(fee_bps=0, fee_currency="USD")
                 live mode  → SessionGuardedAdapter(CcxtExchangeAdapter(alpaca, sandbox))
                              (only when XBT_ALLOW_LIVE=1)
  • passes session → Supervisor
Supervisor._tripped: skip when market closed OR mark stale  (no-op for crypto)
SessionGuardedAdapter.place_order: reject (status="rejected") when market closed
```

---

## Step-by-step plan

### 1. Market-session / calendar core (foundation, pure, no network)
New package `packages/xbt-core/xbt_core/market/` → `session.py`:
- `AssetClass` enum (`crypto`, `us_equity`).
- `MarketSession` Protocol: `is_open(ts_ms) -> bool`, `next_open_ms(ts_ms) -> int | None`.
- `CryptoSession` → always open.
- `UsEquitySession` → Mon–Fri 09:30–16:00 `America/New_York` (stdlib `zoneinfo`),
  minus a hardcoded NYSE holiday set (current + next year, with a "refresh annually"
  comment and a test that fails if it doesn't cover the current year). Avoid heavy deps
  (`pandas-market-calendars`); the `holidays` lib is an optional alternative.
- `session_for(venue: str) -> MarketSession`: `alpaca`→`UsEquitySession`, else
  `CryptoSession`. This is the single resolver the launcher uses.

Mirrors the repo's "pure, testable, parity-friendly" style — deterministic, offline.

### 2. Off-hours order guard (pure wrapper)
New `packages/xbt-core/xbt_core/exchanges/session_guard.py`:
- `SessionGuardedAdapter(ExchangeAdapter, MarketSession)` implementing `ExchangeAdapter`.
- `place_order`: if `session.is_open(now)` is False, return a `PlacementResult` with
  `SubmittedOrder(status="rejected")` and **no** call to the inner adapter — reusing the
  existing `"rejected"` vocabulary so the router persists/emits it gracefully (no
  `bot_error`). Pass through `cancel_order`/`close`/`venue`.
- Only wired for equities; crypto bots get the bare adapter exactly as today.

### 3. Supervisor safety across market gaps
Edit `apps/bot-engine/app/runtime/supervisor.py`:
- `BotHandle`: add `session: MarketSession | None = None`, `last_mark_ts_ms: int | None`,
  `max_mark_age_ms: int | None`.
- `Supervisor.start(...)`: add a `session` kwarg (default `None` = always-open/no gating
  → crypto unchanged).
- `_run`: set `last_mark_ts_ms` alongside `last_mark` each bar.
- `_tripped`: after the existing `cap/last_mark is None` short-circuit, return `False`
  if `session is not None and not session.is_open(now)` **or** if the mark is older than
  `max_mark_age_ms`. Both are no-ops for crypto (session None, fresh marks). This is the
  load-bearing fix: the breaker never acts on a stale overnight mark.
- Add `BotState.IDLE` and emit `bot_idle` / `bot_resumed` lifecycle events when the
  session transitions closed/open (UX so a closed-market bot doesn't look broken).
- `apps/bot-engine/app/api/bots.py`: thread `session` from the `LaunchPlan` into
  `supervisor.start`.

### 4. Launcher wiring (reuse ccxt, add paper/data branch)
Edit `apps/bot-engine/app/launchers/production.py`:
- Resolve `session = session_for(exchange)` and include it in `LaunchPlan` (extend
  `apps/bot-engine/app/api/launcher.py`'s `LaunchPlan` with `session: MarketSession | None`).
- Data feed: for alpaca, build the ccxt data client **with the user's credentials**
  (Alpaca's market-data API requires auth, unlike crypto's keyless public client). This
  is a localized change to the otherwise-keyless data path, gated on `exchange=="alpaca"`.
- Order adapter:
  - paper mode → `PaperExchangeAdapter(venue=exchange, config=PaperConfig(fee_bps=0,
    fee_currency="USD"))` (commission-free, USD settlement). `PaperConfig.fee_currency`
    is already a field — no xbt-core change needed.
  - live mode → `SessionGuardedAdapter(CcxtExchangeAdapter(client, venue="alpaca"),
    session)` where `client` is built via the existing factory with
    `set_sandbox_mode(True)` (→ `paper-api.alpaca.markets`) by default; still behind the
    `XBT_ALLOW_LIVE` gate. Real-money (non-sandbox) is a deliberate later opt-in.
- Crypto venues: unchanged — bare adapter, `session=None`, keyless public data client.

Edit `apps/bot-engine/app/exchanges/ccxt_live.py` `build_ccxt_client` /
`build_public_ccxt_client`: add an alpaca branch that calls
`client.set_sandbox_mode(True)` for the paper endpoint (the `test` URLs confirmed
present). Keep the lazy-import pattern.

### 5. Fractional rounding & min-notional (adapter level)
In `apps/bot-engine/app/exchanges/ccxt_adapter.py` (used for alpaca live): round
`intent.quantity` to venue precision via ccxt's `amount_to_precision` (resolves the
existing float-precision TODO), `ROUND_DOWN`. Let Alpaca reject sub-$1 fractional
notional (maps to `rejected`). Note Alpaca disallows *fractional limit* orders — reject
fractional+limit with a clear status. Strategies stay precision-agnostic.

### 6. Symbol + venue schema (TS ↔ Python parity)
- `packages/shared/src/primitives.ts`: extend `Exchange` enum to add `'alpaca'`; relax
  `Symbol_` to a strict alternation — crypto pair `^[A-Z0-9]+/[A-Z0-9]+$` **or** equity
  ticker `^[A-Z]{1,5}(\.[A-Z])?$` (covers `BRK.B`). Update the message.
- Venue-aware refinement so `AAPL` is rejected on binance and `BTC/USD` on alpaca:
  add a `superRefine` on the start-bot request in `apps/gateway/src/clients/bot.ts`
  (`StartBotRequest` Zod, keyed off `exchange`) and a `model_validator(mode="after")`
  in `apps/bot-engine/app/api/schemas.py` `StartBotRequest`. Asset class is **derived
  from venue** (no new request field) for MVP.
- `packages/xbt-core/xbt_core/strategy_config.py`: `symbol` stays plain `str` (strategy
  layer remains venue-agnostic); validation lives at the request boundary.
- `packages/shared/src/events.ts`: add `bot_idle` / `bot_resumed` to the lifecycle event
  enum (the Python publisher is freeform, so only the TS consumer schema needs it).

### 7. Frontend
- `apps/web/app/dashboard/page.tsx`: add an **exchange selector** (Binance / Coinbase /
  Alpaca) in the "Start a paper bot" card and include `exchange` in the start POST body
  (the gateway already forwards it). Relax `SYMBOL_RE` to match the new `Symbol_`
  (pair-or-ticker, conditioned on selected exchange) and replace the "crypto pairs only
  (no stock tickers)" copy (~lines 21, 161, 263–266) with neutral wording
  (e.g. "BTC/USD, or a stock ticker like AAPL"). Surface the new IDLE state in the
  status display.
- `apps/web/app/settings/page.tsx`: add `alpaca` to the exchange field; Alpaca copy —
  API key ID + secret (no passphrase; hide that field for alpaca), and a note that
  Alpaca issues separate paper keys.

### 8. Copilot prompt
`apps/ai-engine/app/llm/copilot.py` `SYSTEM_PROMPT`: change "automated crypto trading
dashboard" → "automated trading dashboard for crypto and US equities", add one neutral
sentence about equities trading only during US market hours (off-hours orders rejected,
bots idle until reopen). Keep all guardrail language verbatim. No performance claims.

### 9. Backtest
- `apps/ai-engine/app/backtest/data.py`: allow `exchange="alpaca"` in
  `build_ccxt_data_client` (ccxt supports it; same `HistoricalDataClient` Protocol,
  same `candle_to_bar`). No dispatcher needed beyond passing the venue through (already
  wired via `Backtester.run(exchange=...)`).
- `apps/ai-engine/app/backtest/engine.py`: equity OHLCV is already gapless (no rows for
  closed sessions), so `run_backtest` replays correctly; index-based drawdown/return are
  unaffected. Optionally add a defensive `UsEquitySession.is_open` filter on fetched
  candles. Backtest/live parity preserved (same strategies + `PaperExchangeAdapter`).
- If alpaca market data needs auth for historical bars too, the backtest data client
  may need a server-side Alpaca data key (env) since backtests run without user
  credentials — confirm during implementation (see Risks).

### 10. Strategy time math (docs only)
Keep wall-clock interval/cooldown math (least surprising: "every N minutes of market
time, at least once per session"). Update docstrings in
`packages/xbt-core/xbt_core/strategies/dca.py` and `rule_engine.py` to state the
behavior across a market-closed gap. Flag trading-time-aware intervals as post-MVP.

---

## Critical files

New:
- `packages/xbt-core/xbt_core/market/__init__.py`, `market/session.py`
- `packages/xbt-core/xbt_core/exchanges/session_guard.py`
- Tests: `apps/bot-engine/tests/test_market_session.py`,
  `test_session_guard.py`, plus new cases in `test_supervisor.py`,
  `test_production_launcher.py`.

Modified:
- `apps/bot-engine/app/runtime/supervisor.py` (session + stale-mark guards, IDLE state)
- `apps/bot-engine/app/launchers/production.py` + `app/api/launcher.py` (venue dispatch,
  alpaca data-with-keys branch, USD PaperConfig, sandbox live adapter, session in plan)
- `apps/bot-engine/app/api/bots.py` (thread session into start)
- `apps/bot-engine/app/exchanges/ccxt_live.py` (alpaca sandbox/paper client branch)
- `apps/bot-engine/app/exchanges/ccxt_adapter.py` (fractional rounding / precision)
- `apps/bot-engine/app/api/schemas.py` (venue-aware symbol validation)
- `packages/shared/src/primitives.ts`, `events.ts` (Exchange, Symbol_, events)
- `apps/gateway/src/clients/bot.ts` (Zod superRefine for venue↔symbol)
- `apps/web/app/dashboard/page.tsx`, `app/settings/page.tsx` (exchange selector, copy)
- `apps/ai-engine/app/llm/copilot.py` (prompt), `app/backtest/data.py` (+ `engine.py`)
- `packages/xbt-core/xbt_core/strategies/dca.py`, `rule_engine.py` (docstrings)

No new Python dependency required (ccxt already present). `holidays` is optional.

---

## Risks & open items
- **Alpaca market data requires auth** (unlike crypto's keyless public feed). Live bots:
  use the user's stored key for the data client. Backtests run without user creds → may
  need a server-side Alpaca data key (env) or fall back to a crypto data source for the
  backtest preview; confirm ccxt's alpaca `fetchOHLCV` auth behavior during impl.
- **ccxt alpaca paper endpoint**: confirmed via `urls.test` + `set_sandbox_mode`; verify
  end-to-end with real paper keys before trusting the live-order path.
- **Fractional limit unsupported / sub-$1 notional**: enforce/round in the adapter.
- **Holiday list staleness**: hardcoded NYSE holidays + a test asserting current-year
  coverage; or adopt `holidays`.
- **Stale-mark threshold (`max_mark_age_ms`)**: default to the closed-market skip as the
  primary guard; treat the age check as a generous secondary; never apply to crypto.
- **Symbol-regex drift** across `primitives.ts`, web `SYMBOL_RE`, pydantic boundary —
  keep the equity regex strict and extend the CI JSON-Schema parity check.

---

## Verification
- **Unit** (Python): market session (weekends, each holiday, 09:29/09:30/15:59/16:00 ET
  boundaries, a DST day); `SessionGuardedAdapter` reject-closed/pass-open;
  **circuit-breaker-across-gap** in `test_supervisor.py` (loss beyond cap but
  `is_open()==False` → `_tripped` returns False; True once open); fractional rounding;
  crypto regression (session=None → identical to today); launcher selects USD
  PaperConfig + alpaca data client for `exchange="alpaca"`, and the sandbox live adapter
  only when `allow_live=True`.
- **Unit** (TS): `Symbol_` accepts `AAPL` and `BTC/USDT`, rejects `BTC` and `aapl`;
  venue↔symbol superRefine; Exchange enum includes alpaca.
- **Contract**: extend the TS↔Python JSON-Schema parity check for the relaxed Symbol_ /
  Exchange enum.
- **Integration** (marked, skipped without keys): launch a paper bot on `AAPL` with a
  real Alpaca data key during **closed** hours → IDLE, no fills, no error; during
  **open** hours → real bars flow, a DCA buy simulates a fractional fill, and
  `orders`+`fills`+audit rows are written with `exchange="alpaca"`, `fee_currency="USD"`.
  Optional `mode=live` smoke against `paper-api.alpaca.markets` with `XBT_ALLOW_LIVE=1`
  to exercise the real ccxt submit/cancel path.
- **Manual**: dashboard exchange selector works; crypto bot behavior unchanged;
  "crypto only" copy gone.
- Run `pnpm -r typecheck && pnpm -r test` and `.venv/bin/python -m pytest` in both
  Python apps; CI (`node`, `python`, `docker-build`) green.

---

## Out of scope (explicit follow-ups)
Real-money (non-sandbox) Alpaca live trading beyond the gated sandbox path; PDT /
settlement (T+2) modeling; pre/post-market sessions; intraday-halt handling via Alpaca's
clock API; trading-time-aware strategy intervals; websocket (vs polling) market data and
streaming fills.
