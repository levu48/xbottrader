"""Redis Streams publisher. Bot Engine emits, Gateway consumes and fans out to WS."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

STREAM_KEY = "xbt.events"


class StreamWriter(Protocol):
    async def xadd(self, stream: str, fields: dict[str, str], *, maxlen: int | None = None) -> str:
        ...


@dataclass(frozen=True, slots=True)
class Event:
    event_type: str
    user_id: str
    bot_id: str
    payload: dict[str, Any]

    def to_fields(self) -> dict[str, str]:
        return {
            "event_type": self.event_type,
            "user_id": self.user_id,
            "bot_id": self.bot_id,
            "ts": datetime.now(UTC).isoformat(),
            "payload": json.dumps(self.payload, default=str),
        }


class EventPublisher:
    """Publishes events to Redis Streams. Inject a Redis client at construction.

    The stream is capped at ~1M entries by default — older events are dropped.
    Consumers durable-replay via consumer groups (Gateway-side).
    """

    def __init__(self, redis: StreamWriter, *, maxlen: int = 1_000_000) -> None:
        self._redis = redis
        self._maxlen = maxlen

    async def publish(self, event: Event) -> str:
        return await self._redis.xadd(STREAM_KEY, event.to_fields(), maxlen=self._maxlen)
