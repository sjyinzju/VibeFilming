"""Atomic local event journal implementing the existing event bus interface."""

from pathlib import Path
from movie_agent.domain import EventEnvelope
from movie_agent.execution.events import LocalEventBus


class DurableLocalEventBus(LocalEventBus):
    """Single-writer ordered journal; one atomically published file per event."""

    def __init__(self, root: str | Path):
        super().__init__()
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._events = [EventEnvelope.model_validate_json(p.read_text(encoding="utf-8"))
                        for p in sorted(self.root.glob("*.json"))]

    def emit(self, event: EventEnvelope) -> None:
        with self._lock:
            path = self.root / f"{len(self._events):010d}_{event.event_id}.json"
            temporary = path.with_suffix(".tmp")
            temporary.write_text(event.model_dump_json(), encoding="utf-8")
            temporary.replace(path)
            super().emit(event)
