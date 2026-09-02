"""Synchronous local event bus behind a replaceable interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from threading import RLock

from movie_agent.domain import EventEnvelope, EventType

EventHandler = Callable[[EventEnvelope], None]


class EventBus(ABC):
    """Publish/subscribe boundary for production lifecycle events."""

    @abstractmethod
    def emit(self, event: EventEnvelope) -> None: ...

    @abstractmethod
    def subscribe(self, handler: EventHandler, event_type: EventType | None = None) -> None: ...


class LocalEventBus(EventBus):
    """In-memory ordered event log with optional typed subscriptions."""

    def __init__(self) -> None:
        self._events: list[EventEnvelope] = []
        self._subscriptions: list[tuple[EventType | None, EventHandler]] = []
        self._lock = RLock()

    def emit(self, event: EventEnvelope) -> None:
        with self._lock:
            self._events.append(event)
            subscriptions = list(self._subscriptions)
        for event_type, handler in subscriptions:
            if event_type is None or event_type == event.event_type:
                handler(event)

    def subscribe(self, handler: EventHandler, event_type: EventType | None = None) -> None:
        with self._lock:
            self._subscriptions.append((event_type, handler))

    def events(self, event_type: EventType | None = None) -> list[EventEnvelope]:
        """Read the ordered local event stream."""

        with self._lock:
            events = list(self._events)
        return [event for event in events if event_type is None or event.event_type == event_type]

