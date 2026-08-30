"""Drains the transactional outbox into Kafka.

This is the second half of the outbox pattern. The first half - writing the
event in the same database transaction as the postings - is in
app/services/transactions.py and is what makes the event trustworthy. This
half is what makes it *arrive*.

The two halves are deliberately separate processes. The API must never block
on Kafka being reachable: if the broker is down, transactions keep committing
and events pile up unpublished, then drain when it comes back. That is the
whole point of the pattern, and it is why the API has no Kafka client in it.

Delivery is at-least-once. See `publish_batch` for exactly why, and
contracts/events/README.md for what consumers must do about it.
"""
import asyncio
import json
import os

import asyncpg
from aiokafka import AIOKafkaProducer

TOPIC = os.getenv("KAFKA_TOPIC", "ledger.events")
BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092")
POLL_SECONDS = float(os.getenv("PUBLISHER_POLL_SECONDS", "2"))
BATCH_SIZE = int(os.getenv("PUBLISHER_BATCH_SIZE", "100"))


def partition_key(payload: dict) -> bytes:
    """Which Kafka partition an event lands on.

    Kafka only guarantees ordering *within a partition*, and the key is what
    decides the partition. So the key choice is the ordering design.

    Keying a reversal by the transaction it reverses puts it on the same
    partition as that transaction, which means Kafka delivers them in the
    order they were produced. Keying by the event's own transaction_id would
    scatter them and let a reversal overtake its original.

    This is best effort, not a promise. A redelivery after a crash can still
    arrive out of order, which is why the contract tells consumers not to rely
    on arrival order and not to treat "reversal for an unknown transaction" as
    an error.
    """
    data = payload["data"]
    return (data.get("reversal_of_id") or data["transaction_id"]).encode()


def headers_for(payload: dict) -> list[tuple[str, bytes]]:
    """Envelope fields lifted into Kafka headers.

    The payload is still the complete, self-contained envelope - a consumer
    that only reads the value loses nothing. These headers are a convenience:
    they sit outside the serialized value, so a router or an interceptor can
    dispatch on event_type, or drop a duplicate event_id, without paying to
    deserialize the body first.
    """
    return [
        ("event_id", payload["event_id"].encode()),
        ("event_type", payload["event_type"].encode()),
        ("schema_version", str(payload["schema_version"]).encode()),
    ]


async def fetch_unpublished(conn: asyncpg.Connection, limit: int):
    return await conn.fetch("""
        SELECT event_id, payload FROM outbox_events
        WHERE published_at IS NULL
        ORDER BY created_at
        LIMIT $1
    """, limit)


async def mark_published(conn: asyncpg.Connection, event_ids) -> None:
    await conn.execute("""
        UPDATE outbox_events SET published_at = NOW()
        WHERE event_id = ANY($1::uuid[])
    """, event_ids)


async def publish_batch(conn: asyncpg.Connection, producer: AIOKafkaProducer, rows) -> int:
    """Sends a batch to Kafka, then marks it published.

    That order is the entire correctness argument, and reversing it would be
    a silent data-loss bug:

      - Send first, then mark. A crash in between means those events are still
        unpublished, so the next run sends them again. The consumer sees a
        duplicate and deduplicates on event_id. Nothing is lost.

      - Mark first, then send. A crash in between means the rows look
        published but never reached Kafka. They will never be retried, and
        nobody will ever notice. The event is gone.

    At-least-once is not a limitation we settled for - it is the direction
    this trade-off has to fall when the alternative is losing money movement.
    """
    sent = []
    for row in rows:
        payload = json.loads(row["payload"])
        # await means we wait for the broker to acknowledge. Fire-and-forget
        # would let us mark rows published that Kafka never durably stored.
        await producer.send_and_wait(
            TOPIC,
            value=row["payload"].encode(),
            key=partition_key(payload),
            headers=headers_for(payload),
        )
        sent.append(row["event_id"])

    if sent:
        await mark_published(conn, sent)
    return len(sent)


async def run(database_url: str | None = None) -> None:
    """Polls the outbox forever. Stop with Ctrl-C."""
    database_url = database_url or os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set")

    conn = await asyncpg.connect(database_url)
    producer = AIOKafkaProducer(
        bootstrap_servers=BOOTSTRAP_SERVERS,
        # Wait for all in-sync replicas before considering a send successful.
        # With one broker this is the same as acks=1, but it is the setting
        # you want the moment there is more than one.
        acks="all",
        enable_idempotence=True,
    )
    await producer.start()
    print(f"publisher: {BOOTSTRAP_SERVERS} -> topic '{TOPIC}', polling every {POLL_SECONDS}s")

    try:
        while True:
            rows = await fetch_unpublished(conn, BATCH_SIZE)
            if rows:
                count = await publish_batch(conn, producer, rows)
                print(f"  published {count}")
            await asyncio.sleep(POLL_SECONDS)
    finally:
        await producer.stop()
        await conn.close()
