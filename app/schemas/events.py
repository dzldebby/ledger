from typing import Any

from pydantic import BaseModel


class EventFeedPage(BaseModel):
    """One page of the event feed.

    `events` is deliberately untyped. Its shape is the envelope defined in
    contracts/events/README.md, and restating it as a Pydantic model here
    would create a second definition that can silently drift from the
    contract, the fixtures and the contract test. There is one definition and
    it lives in app/services/events.py.
    """
    events: list[dict[str, Any]]
    next_cursor: str | None = None
    count: int
