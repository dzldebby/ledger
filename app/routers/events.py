from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import get_authenticated_client
from app.database import get_conn
from app.schemas.events import EventFeedPage
from app.services.event_feed import DEFAULT_LIMIT, MAX_LIMIT, InvalidCursor, fetch_page

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=EventFeedPage)
async def event_feed(
    cursor: str | None = Query(
        None,
        description="Opaque cursor from a previous response's next_cursor. "
                    "Omit to start from the beginning of the feed.",
    ),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    client_id: str = Depends(get_authenticated_client),
    conn=Depends(get_conn),
):
    """Pull-based feed of ledger events, oldest first.

    An alternative to consuming from the message broker, for consumers that
    cannot reach it. Same envelope, same at-least-once expectations: keep
    deduplicating on `event_id`, because a consumer that crashes after
    processing but before storing `next_cursor` will re-read the page.

    Store `next_cursor` and send it back to continue. The ledger keeps no
    per-consumer state, so consumers cannot interfere with each other and a
    new one can start from the beginning at any time.

    An empty `events` list means you are caught up; poll again later with the
    same cursor.
    """
    try:
        events, next_cursor = await fetch_page(conn, cursor, limit)
    except InvalidCursor:
        raise HTTPException(
            status_code=400,
            detail="Invalid cursor. Pass a next_cursor from a previous "
                   "response, or omit it to start from the beginning.",
        )

    return EventFeedPage(events=events, next_cursor=next_cursor, count=len(events))
