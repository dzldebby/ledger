from pydantic import BaseModel


class OutboxStats(BaseModel):
    total_events: int
    unpublished_events: int
    events_by_type: dict[str, int]
    newest_event_at: str | None = None
