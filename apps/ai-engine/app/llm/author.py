"""Strategy author — turn a plain-English description into a custom_rules config.

The user describes a strategy in words; this module asks Claude (via a *forced*
tool call whose schema mirrors the rule-engine DSL) to emit a structured config,
then validates it through the same ``CustomRulesParams`` + ``RuleEngineParams``
the live path uses. Because the output is an ordinary ``custom_rules`` config, it
runs deterministically through the existing ``RuleEngineStrategy`` — no LLM at
runtime, full backtest/live parity. The AI only authors; it never trades.

Validation is a closed loop: if the emitted config fails to validate or to build,
the error is fed back to Claude and it retries (capped). A returned config is one
that is guaranteed to build and run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from xbt_core.strategies.rule_engine import RuleEngineParams
from xbt_core.strategy_config import CustomRulesParams

# (system_blocks, messages, model, max_tokens, tools, tool_choice)
#   -> (tool_input dict, usage). Distinct from copilot's text CompleteFn — this
# one forces a tool call and returns its validated input object.
ToolCompleteFn = Callable[
    [list[dict[str, Any]], list[dict[str, Any]], str, int, list[dict[str, Any]], dict[str, Any]],
    "tuple[dict[str, Any], dict[str, int]]",
]

DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 2048
MAX_REPAIRS = 2  # retries after a validation/build failure

_TOOL_NAME = "emit_strategy"

SYSTEM_PROMPT = """\
You translate a plain-English trading-strategy description into a structured \
rule-engine config for the xbottrader platform, by calling the emit_strategy tool. \
You never place trades or give financial advice — you only express the user's \
described logic as data.

The rule engine evaluates indicators against the bar stream and fires actions:

Indicators (each has a unique `name` you choose, and an `fn`):
- "price"  — the current bar close (also always available as the reserved name `price`; you do not need to declare it).
- "value"  — a constant; set `value`.
- "sma"    — simple moving average; set `period` (>= 2).
- "rsi"    — Wilder-free simple RSI; set `period` (>= 2).
Only these four exist. Do NOT invent EMA, MACD, Bollinger, etc.

Conditions (the `when` of a rule):
- Comparison: {"op": "<"|"<="|">"|">="|"=="|"crossover"|"crossunder", "left": <name-or-number>, "right": <name-or-number>}
  `crossover` = left was <= right and is now > right (edge-triggered). `crossunder` is the mirror.
  Operands are indicator names you declared, the reserved `price`, or numeric literals as strings ("30").
- Boolean: {"op": "and"|"or"|"not", "terms": [<condition>, ...]}  ("not" takes exactly one term).

Actions (the `do` of a rule):
- {"side": "buy"|"sell", "type": "market"|"limit", "quote": <positive notional to spend/unwind>, "limit_offset_pct": <required only for limit, percent vs close>}

Each rule may set `cooldown_minutes` (default 0) to rate-limit re-firing. Rules are \
edge-triggered: they fire on the bar a condition flips false->true, not every bar it stays true.

Guidance: pick sensible periods and notionals if the user is vague, and explain your \
choices in the `explanation` field. Keep it to the minimum indicators and rules needed."""


_CONDITION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "op": {
            "type": "string",
            "enum": ["<", "<=", ">", ">=", "==", "crossover", "crossunder", "and", "or", "not"],
        },
        "left": {"type": "string", "description": "indicator name, reserved 'price', or numeric literal"},
        "right": {"type": "string", "description": "indicator name, reserved 'price', or numeric literal"},
        "terms": {
            "type": "array",
            "items": {"$ref": "#/$defs/condition"},
            "description": "sub-conditions for and/or/not",
        },
    },
    "required": ["op"],
}

_TOOL_SCHEMA: dict[str, Any] = {
    "name": _TOOL_NAME,
    "description": "Emit the structured rule-engine strategy that expresses the user's description.",
    "input_schema": {
        "type": "object",
        "$defs": {"condition": _CONDITION_SCHEMA},
        "properties": {
            "indicators": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "fn": {"type": "string", "enum": ["price", "value", "sma", "rsi"]},
                        "period": {"type": "integer"},
                        "value": {"type": "string", "description": "constant value (decimal string) for fn=value"},
                    },
                    "required": ["name", "fn"],
                },
            },
            "rules": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "when": {"$ref": "#/$defs/condition"},
                        "do": {
                            "type": "object",
                            "properties": {
                                "side": {"type": "string", "enum": ["buy", "sell"]},
                                "type": {"type": "string", "enum": ["market", "limit"]},
                                "quote": {"type": "string", "description": "notional, decimal string"},
                                "limit_offset_pct": {"type": "string"},
                            },
                            "required": ["side", "quote"],
                        },
                        "cooldown_minutes": {"type": "integer"},
                    },
                    "required": ["when", "do"],
                },
            },
            "explanation": {
                "type": "string",
                "description": "Plain-English summary of the strategy and any choices you made.",
            },
        },
        "required": ["rules", "explanation"],
    },
}


@dataclass(frozen=True, slots=True)
class AuthorResult:
    strategy: CustomRulesParams
    explanation: str
    usage: dict[str, int] = field(default_factory=dict)


class StrategyAuthorError(ValueError):
    """The model could not produce a valid config within the repair budget."""


class StrategyAuthor:
    def __init__(
        self,
        complete: ToolCompleteFn,
        *,
        model: str = DEFAULT_MODEL,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self._complete = complete
        self._model = model
        self._system_prompt = system_prompt

    def author(self, *, description: str, symbol: str) -> AuthorResult:
        system_blocks = [
            {"type": "text", "text": self._system_prompt, "cache_control": {"type": "ephemeral"}}
        ]
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": (
                    f"Symbol: {symbol}\n\nDescription:\n{description}\n\n"
                    "Call emit_strategy with the config that captures this."
                ),
            }
        ]
        tool_choice = {"type": "tool", "name": _TOOL_NAME}

        total_usage: dict[str, int] = {}
        last_error = ""
        for _ in range(MAX_REPAIRS + 1):
            tool_input, usage = self._complete(
                system_blocks, messages, self._model, MAX_TOKENS, [_TOOL_SCHEMA], tool_choice
            )
            _accumulate(total_usage, usage)
            try:
                strategy = _validate(tool_input, symbol)
            except (ValueError, KeyError, TypeError) as e:
                last_error = str(e)
                # Feed the failed attempt + the error back so the model repairs it.
                messages.append({"role": "assistant", "content": _summarize_attempt(tool_input)})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"That config is invalid: {last_error}. "
                            "Call emit_strategy again with a corrected config."
                        ),
                    }
                )
                continue
            explanation = str(tool_input.get("explanation", "")).strip()
            return AuthorResult(strategy=strategy, explanation=explanation, usage=total_usage)

        raise StrategyAuthorError(f"could not author a valid strategy: {last_error}")


def _validate(tool_input: dict[str, Any], symbol: str) -> CustomRulesParams:
    """Validate the model's tool input through the same models the live path uses."""
    data = {
        "strategy_type": "custom_rules",
        "symbol": symbol,
        "indicators": tool_input.get("indicators", []),
        "rules": tool_input.get("rules", []),
    }
    params = CustomRulesParams(**data)  # structural + indicator-reference validation
    RuleEngineParams.from_config(params)  # build-time validation (same as the factory)
    return params


def _summarize_attempt(tool_input: dict[str, Any]) -> str:
    # Keep the assistant turn small; the model just needs to see what it sent.
    n_ind = len(tool_input.get("indicators", []) or [])
    n_rules = len(tool_input.get("rules", []) or [])
    return f"(previous attempt: {n_ind} indicators, {n_rules} rules)"


def _accumulate(total: dict[str, int], usage: dict[str, int]) -> None:
    for k, v in usage.items():
        total[k] = total.get(k, 0) + int(v)


def build_anthropic_tool_complete(api_key: str) -> ToolCompleteFn:
    """Wrap the Anthropic SDK as a forced-tool-call ToolCompleteFn. Lazy import."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)

    def complete(
        system_blocks: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int,
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, int]]:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_blocks,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
        )
        tool_input: dict[str, Any] = {}
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                tool_input = dict(block.input)  # SDK already parsed the JSON
                break
        u = resp.usage
        usage = {
            "input_tokens": getattr(u, "input_tokens", 0),
            "output_tokens": getattr(u, "output_tokens", 0),
            "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
        }
        return tool_input, usage

    return complete
