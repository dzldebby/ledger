"""Read-only views over the transactional outbox.

The deployed database is private and unreachable from outside AWS, so without
something like this the only way to inspect the outbox is to temporarily
attach a container to the running service. These are aggregates only - no
payloads, no account identifiers - so the endpoint exposes operational
information rather than ledger data.
"""
import asyncpg

from app.schemas.outbox import OutboxStats


async def get_stats(conn: asyncpg.Connection) -> OutboxStats:
    row = await conn.fetchrow("""
        SELECT
            count(*)                                   AS total_events,
            count(*) FILTER (WHERE published_at IS NULL) AS unpublished_events,
            max(created_at)                            AS newest_event_at
        FROM outbox_events
    """)

    by_type = await conn.fetch("""
        SELECT event_type, count(*) AS n
        FROM outbox_events
        GROUP BY event_type
        ORDER BY event_type
    """)

    newest = row["newest_event_at"]
    return OutboxStats(
        total_events=row["total_events"],
        unpublished_events=row["unpublished_events"],
        events_by_type={r["event_type"]: r["n"] for r in by_type},
        newest_event_at=newest.isoformat() if newest else None,
    )
