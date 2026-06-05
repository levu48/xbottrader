"""Trading copilot over the Anthropic API.

A thin wrapper that builds a cached system prompt and forwards chat turns to a
``CompleteFn`` — the real one wraps the Anthropic SDK; tests inject a fake. The
large, reused system prompt is marked with ``cache_control`` so repeated calls
hit the prompt cache (per the plan: "enable prompt caching — the bot-builder
system prompt is large and reused").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

# (system_blocks, messages, model, max_tokens) -> (reply_text, usage)
CompleteFn = Callable[[list[dict[str, Any]], list[dict[str, Any]], str, int], "tuple[str, dict[str, int]]"]

DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1024

SYSTEM_PROMPT = """\
You are the xbottrader copilot, an assistant embedded in an automated crypto \
trading dashboard. You help users understand their bots: explain how a strategy \
(DCA, grid, MA-crossover) works, summarize PnL and fills, and answer "why did my \
bot do X". Be concise and concrete. You never place, modify, or cancel trades and \
you never give individualized financial advice or return guarantees — if asked to, \
explain that bots are configured by the user and trading carries risk. When given \
a context block with the user's bot/PnL data, ground your answer in it."""


@dataclass(frozen=True, slots=True)
class CopilotResult:
    reply: str
    usage: dict[str, int] = field(default_factory=dict)


class Copilot:
    def __init__(
        self,
        complete: CompleteFn,
        *,
        model: str = DEFAULT_MODEL,
        system_prompt: str = SYSTEM_PROMPT,
    ) -> None:
        self._complete = complete
        self._model = model
        self._system_prompt = system_prompt

    def chat(
        self, messages: list[dict[str, Any]], *, context: str | None = None
    ) -> CopilotResult:
        # Cache the (large, stable) system prompt across calls.
        system_blocks: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": self._system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        turns = list(messages)
        if context:
            turns = [{"role": "user", "content": f"Context for this conversation:\n{context}"}, *turns]
        reply, usage = self._complete(system_blocks, turns, self._model, MAX_TOKENS)
        return CopilotResult(reply=reply, usage=usage)


def build_anthropic_complete(api_key: str) -> CompleteFn:
    """Wrap the Anthropic SDK as a CompleteFn. Imported lazily to keep boot light."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)

    def complete(
        system_blocks: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int,
    ) -> tuple[str, dict[str, int]]:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_blocks,
            messages=messages,
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        u = resp.usage
        usage = {
            "input_tokens": getattr(u, "input_tokens", 0),
            "output_tokens": getattr(u, "output_tokens", 0),
            "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
        }
        return text, usage

    return complete
