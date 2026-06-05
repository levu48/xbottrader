from __future__ import annotations

from app.llm.copilot import SYSTEM_PROMPT, Copilot


def test_chat_caches_system_prompt_and_returns_reply() -> None:
    captured: dict = {}

    def fake_complete(system_blocks, messages, model, max_tokens):
        captured["system"] = system_blocks
        captured["messages"] = messages
        captured["model"] = model
        return "hello back", {"input_tokens": 10, "output_tokens": 5}

    cop = Copilot(fake_complete, model="claude-test")
    res = cop.chat([{"role": "user", "content": "explain my DCA bot"}], context="PnL: +5%")

    assert res.reply == "hello back"
    assert res.usage["input_tokens"] == 10
    assert captured["model"] == "claude-test"
    # System prompt is sent as a cached block.
    assert captured["system"][0]["text"] == SYSTEM_PROMPT
    assert captured["system"][0]["cache_control"] == {"type": "ephemeral"}
    # Context is prepended ahead of the user's turn.
    assert captured["messages"][0]["role"] == "user"
    assert "PnL: +5%" in captured["messages"][0]["content"]
    assert captured["messages"][-1]["content"] == "explain my DCA bot"


def test_chat_without_context_passes_messages_through() -> None:
    def fake_complete(system_blocks, messages, model, max_tokens):
        return "ok", {}

    cop = Copilot(fake_complete)
    res = cop.chat([{"role": "user", "content": "hi"}])
    assert res.reply == "ok"
