"""Declarative rule-engine strategy — user-authored logic as *data*, not code.

A user composes a strategy from a fixed vocabulary of indicators and conditions,
serialized as JSON (stored as JSONB, sent over the API). This module interprets
that data in :meth:`on_bar`. Because there is no ``eval`` and no user code, the
same JSON runs identically in the backtester and in live execution with **zero
sandboxing** — it inherits the pure-``Strategy`` parity contract for free.

DSL shape (the dict this module parses)::

    {
      "symbol": "BTC/USDT",
      "indicators": [
        {"name": "fast", "fn": "sma", "period": 10},
        {"name": "slow", "fn": "sma", "period": 30},
        {"name": "rsi14", "fn": "rsi", "period": 14}
      ],
      "rules": [
        {"when": {"op": "and", "terms": [
            {"op": "crossover", "left": "fast", "right": "slow"},
            {"op": "<", "left": "rsi14", "right": "30"}]},
         "do": {"side": "buy", "type": "market", "quote": "100"},
         "cooldown_minutes": 60}
      ]
    }

Deliberate v1 constraints (each keeps the parity invariant or bounds risk):

- **Window-bounded indicators only** — ``price``, ``value`` (constant), ``sma``,
  ``rsi`` (simple-average form). Each depends only on the last *N* closes, so a
  live bot that starts mid-stream converges to the *exact* values a full-history
  backtest produces once warmed up. Path-dependent indicators (EMA, Wilder-RSI)
  are deferred precisely because they'd break that parity without a shared warmup
  convention.
- **Edge-triggered rules** — a rule fires on the bar its condition flips
  false→true, not every bar it stays true. ``crossover`` is naturally edge-only;
  for level conditions (``rsi < 30``) this means "fire once on entry", which is
  almost always what a user means. ``cooldown_minutes`` further rate-limits
  (measured against bar timestamps; an equity market-closed gap counts toward the
  cooldown, so it simply elapses across the gap).
- **No inventory guard** — a ``sell`` rule does not check that there is anything
  to sell; overselling is caught downstream by the exchange adapter and the
  Supervisor's risk limits, not here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .base import Bar, OrderIntent, Strategy, StrategyState

_COMPARATORS = {"<", "<=", ">", ">=", "==", "crossover", "crossunder"}
_BOOL_OPS = {"and", "or", "not"}
_INDICATOR_FNS = {"price", "value", "sma", "rsi"}
# Operand always available without being declared — the current bar close.
_RESERVED_OPERANDS = {"price"}


# --------------------------------------------------------------------------- #
# Config dataclasses (the canonical, pydantic-free representation).
# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class IndicatorDef:
    name: str
    fn: str
    period: int = 0
    value: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("indicator name is required")
        if self.fn not in _INDICATOR_FNS:
            raise ValueError(f"unknown indicator fn: {self.fn!r}")
        if self.fn in ("sma", "rsi") and self.period < 2:
            raise ValueError(f"{self.fn} requires period >= 2")

    @property
    def window(self) -> int:
        """How many trailing closes this indicator needs to produce a value."""
        if self.fn == "rsi":
            return self.period + 1  # needs `period` deltas
        if self.fn == "sma":
            return self.period
        return 1  # price / value


@dataclass(frozen=True, slots=True)
class Compare:
    op: str  # one of _COMPARATORS
    left: str  # indicator name or numeric literal
    right: str


@dataclass(frozen=True, slots=True)
class BoolOp:
    op: str  # "and" | "or" | "not"
    terms: tuple["Condition", ...]


Condition = Compare | BoolOp


@dataclass(frozen=True, slots=True)
class Action:
    side: str  # "buy" | "sell"
    type: str  # "market" | "limit"
    quote: Decimal  # notional to spend (buy) / unwind (sell)
    limit_offset_pct: Decimal | None = None  # vs close; +above / -below

    def __post_init__(self) -> None:
        if self.side not in ("buy", "sell"):
            raise ValueError(f"bad side: {self.side!r}")
        if self.type not in ("market", "limit"):
            raise ValueError(f"bad order type: {self.type!r}")
        if self.quote <= 0:
            raise ValueError("action quote must be positive")
        if self.type == "limit" and self.limit_offset_pct is None:
            raise ValueError("limit action requires limit_offset_pct")


@dataclass(frozen=True, slots=True)
class Rule:
    when: Condition
    do: Action
    cooldown_minutes: int = 0


@dataclass(frozen=True, slots=True)
class RuleEngineParams:
    symbol: str
    indicators: tuple[IndicatorDef, ...]
    rules: tuple[Rule, ...]

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol is required")
        if not self.rules:
            raise ValueError("at least one rule is required")
        names = {i.name for i in self.indicators}
        if len(names) != len(self.indicators):
            raise ValueError("indicator names must be unique")
        if names & _RESERVED_OPERANDS:
            raise ValueError(f"indicator name is reserved: {names & _RESERVED_OPERANDS}")
        known = names | _RESERVED_OPERANDS
        # Every indicator referenced by a condition must be defined (or reserved).
        for rule in self.rules:
            for token in _referenced_names(rule.when):
                if token not in known and not _is_literal(token):
                    raise ValueError(f"rule references unknown indicator: {token!r}")

    @property
    def max_window(self) -> int:
        return max((i.window for i in self.indicators), default=1)

    # -- parsing from JSON-ish data (dicts/lists) ------------------------- #
    @classmethod
    def from_config(cls, config: object) -> "RuleEngineParams":
        """Build from a duck-typed config (pydantic model, dataclass, or dict).

        Pydantic models are dumped to nested dicts first; everything else is
        coerced to a mapping. Parsing then proceeds purely over dicts/lists, so
        xbt_core stays free of any pydantic dependency.
        """
        data = config.model_dump() if hasattr(config, "model_dump") else _as_mapping(config)
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, d: Mapping[str, object]) -> "RuleEngineParams":
        return cls(
            symbol=str(d["symbol"]),
            indicators=tuple(_parse_indicator(i) for i in _seq(d["indicators"])),
            rules=tuple(_parse_rule(r) for r in _seq(d["rules"])),
        )


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #
def _as_mapping(obj: object) -> Mapping[str, object]:
    if isinstance(obj, Mapping):
        return obj
    return vars(obj)


def _seq(obj: object) -> Sequence[object]:
    if isinstance(obj, Sequence) and not isinstance(obj, (str, bytes)):
        return obj
    raise ValueError("expected a list")


def _dec(x: object) -> Decimal:
    return Decimal(str(x))


def _is_literal(token: str) -> bool:
    try:
        Decimal(token)
        return True
    except (InvalidOperation, ValueError):
        return False


def _parse_indicator(o: object) -> IndicatorDef:
    m = _as_mapping(o)
    return IndicatorDef(
        name=str(m["name"]),
        fn=str(m["fn"]),
        period=int(m.get("period", 0)),  # type: ignore[arg-type]
        value=_dec(m.get("value", 0)),
    )


def _parse_condition(o: object) -> Condition:
    m = _as_mapping(o)
    op = str(m["op"])
    if op in _BOOL_OPS:
        terms = tuple(_parse_condition(t) for t in _seq(m["terms"]))
        if op == "not" and len(terms) != 1:
            raise ValueError("'not' takes exactly one term")
        if op in ("and", "or") and not terms:
            raise ValueError(f"'{op}' needs at least one term")
        return BoolOp(op=op, terms=terms)
    if op in _COMPARATORS:
        return Compare(op=op, left=str(m["left"]), right=str(m["right"]))
    raise ValueError(f"unknown condition op: {op!r}")


def _parse_rule(o: object) -> Rule:
    m = _as_mapping(o)
    do = _as_mapping(m["do"])
    offset = do.get("limit_offset_pct")
    action = Action(
        side=str(do["side"]),
        type=str(do.get("type", "market")),
        quote=_dec(do["quote"]),
        limit_offset_pct=(_dec(offset) if offset is not None else None),
    )
    return Rule(
        when=_parse_condition(m["when"]),
        do=action,
        cooldown_minutes=int(m.get("cooldown_minutes", 0)),  # type: ignore[arg-type]
    )


def _referenced_names(cond: Condition) -> set[str]:
    if isinstance(cond, BoolOp):
        out: set[str] = set()
        for t in cond.terms:
            out |= _referenced_names(t)
        return out
    return {cond.left, cond.right}


# --------------------------------------------------------------------------- #
# Strategy
# --------------------------------------------------------------------------- #
class RuleEngineStrategy(Strategy):
    """Interprets a :class:`RuleEngineParams` against the bar stream."""

    def __init__(self, params: RuleEngineParams) -> None:
        self._p = params

    def on_bar(self, bar: Bar, state: StrategyState) -> list[OrderIntent]:
        if bar.symbol != self._p.symbol or bar.close <= 0:
            return []

        # Maintain a trailing window of closes just large enough for every
        # indicator. Bounded size is what keeps live/backtest values identical.
        closes: list[Decimal] = state.custom.setdefault("closes", [])  # type: ignore[assignment]
        closes.append(bar.close)
        if len(closes) > self._p.max_window:
            del closes[0]

        cur = self._indicator_values(closes)
        prev: dict[str, Decimal | None]
        prev = state.custom.get("prev_ind", {})  # type: ignore[assignment]
        prev_truth: dict[int, bool]
        prev_truth = state.custom.setdefault("rule_truth", {})  # type: ignore[assignment]
        last_fire: dict[int, int]
        last_fire = state.custom.setdefault("rule_fire_ts", {})  # type: ignore[assignment]

        intents: list[OrderIntent] = []
        for idx, rule in enumerate(self._p.rules):
            truth = _eval(rule.when, cur, prev)
            rose = truth and not prev_truth.get(idx, False)
            prev_truth[idx] = truth
            if not rose:
                continue
            if rule.cooldown_minutes > 0:
                prior = last_fire.get(idx)
                if prior is not None and bar.ts_ms - prior < rule.cooldown_minutes * 60_000:
                    continue
            intent = self._intent(rule, bar)
            if intent is not None:
                last_fire[idx] = bar.ts_ms
                intents.append(intent)

        state.custom["prev_ind"] = cur
        return intents

    # -- internals -------------------------------------------------------- #
    def _indicator_values(self, closes: list[Decimal]) -> dict[str, Decimal | None]:
        vals: dict[str, Decimal | None] = {"price": closes[-1]}  # reserved built-in
        vals.update({ind.name: _compute(ind, closes) for ind in self._p.indicators})
        return vals

    def _intent(self, rule: Rule, bar: Bar) -> OrderIntent | None:
        action = rule.do
        if action.type == "limit":
            assert action.limit_offset_pct is not None
            price = bar.close * (Decimal(1) + action.limit_offset_pct / Decimal(100))
            if price <= 0:
                return None
            qty = action.quote / price
            return OrderIntent(
                symbol=self._p.symbol, side=action.side, type="limit",
                quantity=qty, limit_price=price, client_tag=f"rule-limit:{bar.ts_ms}",
            )
        qty = action.quote / bar.close
        return OrderIntent(
            symbol=self._p.symbol, side=action.side, type="market",
            quantity=qty, client_tag=f"rule:{bar.ts_ms}",
        )


# --------------------------------------------------------------------------- #
# Indicator + condition evaluation (pure functions)
# --------------------------------------------------------------------------- #
def _compute(ind: IndicatorDef, closes: list[Decimal]) -> Decimal | None:
    if ind.fn == "value":
        return ind.value
    if ind.fn == "price":
        return closes[-1]
    if ind.fn == "sma":
        if len(closes) < ind.period:
            return None
        window = closes[-ind.period :]
        return sum(window, Decimal(0)) / ind.period
    if ind.fn == "rsi":
        if len(closes) < ind.period + 1:
            return None
        recent = closes[-(ind.period + 1) :]
        deltas = [recent[i] - recent[i - 1] for i in range(1, len(recent))]
        gains = sum((d for d in deltas if d > 0), Decimal(0)) / ind.period
        losses = sum((-d for d in deltas if d < 0), Decimal(0)) / ind.period
        if losses == 0:
            return Decimal(100) if gains > 0 else Decimal(50)
        rs = gains / losses
        return Decimal(100) - Decimal(100) / (Decimal(1) + rs)
    return None  # unreachable — fn validated at construction


def _operand(token: str, vals: Mapping[str, Decimal | None]) -> Decimal | None:
    if _is_literal(token):
        return Decimal(token)
    return vals.get(token)


def _eval(
    cond: Condition,
    cur: Mapping[str, Decimal | None],
    prev: Mapping[str, Decimal | None],
) -> bool:
    if isinstance(cond, BoolOp):
        if cond.op == "not":
            return not _eval(cond.terms[0], cur, prev)
        if cond.op == "and":
            return all(_eval(t, cur, prev) for t in cond.terms)
        return any(_eval(t, cur, prev) for t in cond.terms)

    lc, rc = _operand(cond.left, cur), _operand(cond.right, cur)
    if cond.op in ("crossover", "crossunder"):
        lp, rp = _operand(cond.left, prev), _operand(cond.right, prev)
        if None in (lc, rc, lp, rp):
            return False
        if cond.op == "crossover":
            return lp <= rp and lc > rc  # type: ignore[operator]
        return lp >= rp and lc < rc  # type: ignore[operator]

    if lc is None or rc is None:
        return False  # not enough data to compare yet
    if cond.op == "<":
        return lc < rc
    if cond.op == "<=":
        return lc <= rc
    if cond.op == ">":
        return lc > rc
    if cond.op == ">=":
        return lc >= rc
    return lc == rc  # "=="
