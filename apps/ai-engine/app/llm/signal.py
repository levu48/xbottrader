"""Signal advisor — the LLM half of the ai_signal strategy.

Given a window of recent closes, the current position, and the user's guidance,
Claude returns a single discrete decision: buy, sell, or hold. It deliberately
does NOT size the trade — sizing stays in the strategy config (quote_amount), so
the model's authority is bounded to direction only. The decision is produced via
a *forced* tool call so the output is always one of the three actions.

This is consulted live (by the Bot Engine's poller) on the bot's decision
interval — never in a backtest, which is why ai_signal is not backtestable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .author import ToolCompleteFn  # same forced-tool-call shape

DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 512
_TOOL_NAME = "decide"

SYSTEM_PROMPT = """\
You are the decision engine for an automated trading bot on the xbottrader \
platform. On each call you are given recent price history for one symbol, the \
bot's current position, and the user's strategy guidance. Decide the single next \
action: "buy", "sell", or "hold". You do not choose trade size — the platform \
sizes orders from the user's configured notional. Prefer "hold" unless the data \
and the user's guidance clearly support acting. You never give financial advice \
to a human; you only emit a machine decision for this configured bot. Trading \
carries risk and you provide no guarantees."""

_TOOL_SCHEMA: dict[str, Any] = {
    "name": _TOOL_NAME,
    "description": "Emit the next trading action for this bot.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["buy", "sell", "hold"]},
            "reason": {"type": "string", "description": "One short sentence justifying the action."},
        },
        "required": ["action", "reason"],
    },
}


@dataclass(frozen=True, slots=True)
class SignalResult:
    action: str  # "buy" | "sell" | "hold"
    reason: str
    usage: dict[str, int] = field(default_factory=dict)


class SignalAdvisor:
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

    def decide(
        self,
        *,
        symbol: str,
        closes: list[Decimal],
        position: Decimal,
        guidance: str | None = None,
        model: str | None = None,
    ) -> SignalResult:
        system_blocks = [
            {"type": "text", "text": self._system_prompt, "cache_control": {"type": "ephemeral"}}
        ]
        # Compact, deterministic prompt body. Closes are oldest->newest.
        closes_str = ", ".join(str(c) for c in closes)
        user = (
            f"Symbol: {symbol}\n"
            f"Current position: {position}\n"
            f"User guidance: {guidance or '(none)'}\n"
            f"Recent closes (oldest to newest): {closes_str}\n\n"
            "Call decide with the next action."
        )
        messages = [{"role": "user", "content": user}]
        tool_choice = {"type": "tool", "name": _TOOL_NAME}

        tool_input, usage = self._complete(
            system_blocks, messages, model or self._model, MAX_TOKENS, [_TOOL_SCHEMA], tool_choice
        )
        action = tool_input.get("action")
        if action not in ("buy", "sell", "hold"):
            action = "hold"  # defensive: never act on a malformed decision
        reason = str(tool_input.get("reason", "")).strip()
        return SignalResult(action=action, reason=reason, usage=usage)
