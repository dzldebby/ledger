"""A pull-based event feed - the alternative transport to Kafka.

A consumer polls `GET /events`, processes what it gets, and sends back the
`next_cursor` to continue. The cursor is theirs to store; the ledger keeps no
per-consumer state, so any number of consumers can read at their own pace.

This is independent of `published_at`, which tracks Kafka delivery only. An
event can be published to Kafka, served over this feed, both, or neither. Two
transports, two positions, neither aware of the other.

Two bugs are avoided deliberately, and both are the kind that silently drop
events rather than raising anything.
"""
import base64
import binascii
from datetime import datetime, timedelta, timezone

import asyncpg

from app.services.events import format_timestamp

DEFAULT_LIMIT = 100
MAX_LIMIT = 1000

# --------------------------------------------------------------------------
# BUG 2: the late-commit gap.
#
# created_at defaults to now(), which in Postgres is the *transaction start*
# time, not the commit time. A transaction that begins at 10:00:00 and commits
# at 10:00:05 writes a row stamped 10:00:00 that only becomes visible at
# 10:00:05. A consumer that polled at 10:00:03 and advanced its cursor to
# 10:00:04 will never look back that far - the event is skipped permanently,
# with no error anywhere.
#
# Withholding rows younger than this window fixes it: by the time a row is
# served, any transaction that started before it has already committed or
# rolled back. The cost is up to this much feed latency.
#
# It is safe as long as no write transaction runs longer than the window.
# Ledger writes lock two balance rows and commit in milliseconds, so two
# seconds is a large margin. A system with long transactions would need a
# bigger window, or proper xmin-snapshot tracking of the sort Debezium does.
SAFETY_WINDOW = timedelta(seconds=2)


class InvalidCursor(ValueError):
    pass


def encode_cursor(created_at: datetime, event_id) -> str:
    """Opaque to consumers so the internals can change without breaking them.

    Base64 rather than encryption - it hides nothing sensitive, it just stops
    people hand-constructing cursors and depending on the format.
    """
    raw = f"{created_at.isoformat()}|{event_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        timestamp, event_id = raw.split("|", 1)
        return datetime.fromisoformat(timestamp), event_id
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidCursor(str(exc)) from exc


async def fetch_page(conn: asyncpg.Connection, cursor: str | None, limit: int):
    """One page of events, ordered and gap-free.

    BUG 1: ties on the timestamp.

    Three events committed in the same second carry the same created_at.
    Paginating on the timestamp alone forces a choice between two broken
    options: `> last_seen` skips any same-second events not yet read, and
    `>= last_seen` re-reads the same page forever.

    The fix is a compound key. (created_at, event_id) is unique because
    event_id is, so the ordering is total and `>` is exact - no ties to break,
    nothing skipped, nothing repeated. Postgres compares row values
    left-to-right, so `(created_at, event_id) > ($1, $2)` means "later second,
    OR same second and a higher event_id".

    This is keyset pagination. It is also why a Kafka offset is such a
    pleasant thing to have: one monotonic integer, no ties possible.
    """
    limit = max(1, min(limit, MAX_LIMIT))
    horizon = datetime.now(timezone.utc) - SAFETY_WINDOW

    if cursor:
        after_time, after_id = decode_cursor(cursor)
        rows = await conn.fetch("""
            SELECT event_id, created_at, payload FROM outbox_events
            WHERE (created_at, event_id) > ($1, $2::uuid)
              AND created_at <= $3
            ORDER BY created_at, event_id
            LIMIT $4
        """, after_time, after_id, horizon, limit)
    else:
        rows = await conn.fetch("""
            SELECT event_id, created_at, payload FROM outbox_events
            WHERE created_at <= $1
            ORDER BY created_at, event_id
            LIMIT $2
        """, horizon, limit)

    # A cursor is returned whenever the page is non-empty, including a short
    # page. "Fewer than limit" does not mean "caught up" - it may just mean
    # the rest are still inside the safety window. A consumer polls again with
    # the cursor and gets them a moment later.
    next_cursor = encode_cursor(rows[-1]["created_at"], rows[-1]["event_id"]) if rows else cursor
    return [row["payload"] for row in rows], next_cursor
