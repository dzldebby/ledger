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
import logging
import os
import time

import asyncpg
from aiokafka import AIOKafkaProducer
from opentelemetry import metrics, trace
from opentelemetry.metrics import Observation
from opentelemetry.propagate import extract, inject
from opentelemetry.trace import SpanKind

from app.telemetry import configure_telemetry, correlation_id_var

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("outbox-publisher")
meter = metrics.get_meter("outbox-publisher")
published_counter = meter.create_counter("outbox.events.published")
publish_duration = meter.create_histogram("outbox.publish.duration", unit="ms")
backlog_histogram = meter.create_histogram("outbox.backlog")
oldest_age_histogram = meter.create_histogram("outbox.oldest_unpublished.age", unit="s")
_backlog_value = 0
_oldest_age_value = 0.0


def _observe_backlog_value(options):
    return [Observation(_backlog_value)]


def _observe_oldest_age_value(options):
    return [Observation(_oldest_age_value)]


meter.create_observable_gauge(
    "outbox.backlog.current", callbacks=[_observe_backlog_value]
)
meter.create_observable_gauge(
    "outbox.oldest_unpublished.current_age",
    callbacks=[_observe_oldest_age_value],
    unit="s",
)

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
    carrier = {}
    inject(carrier)
    headers = [
        ("event_id", payload["event_id"].encode()),
        ("event_type", payload["event_type"].encode()),
        ("schema_version", str(payload["schema_version"]).encode()),
        ("correlation_id", payload["correlation_id"].encode()),
    ]
    if carrier.get("traceparent"):
        headers.append(("traceparent", carrier["traceparent"].encode()))
    return headers


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


async def observe_backlog(conn: asyncpg.Connection) -> None:
    global _backlog_value, _oldest_age_value
    row = await conn.fetchrow("""
        SELECT count(*) AS backlog,
               EXTRACT(EPOCH FROM clock_timestamp() - min(created_at)) AS oldest_age
        FROM outbox_events WHERE published_at IS NULL
    """)
    _backlog_value = row["backlog"]
    _oldest_age_value = float(row["oldest_age"] or 0)
    backlog_histogram.record(_backlog_value)
    oldest_age_histogram.record(_oldest_age_value)


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
        carrier = {}
        if payload.get("traceparent"):
            carrier["traceparent"] = payload["traceparent"]
        parent_context = extract(carrier)
        token = correlation_id_var.set(payload.get("correlation_id"))
        started = time.perf_counter()
        try:
            with tracer.start_as_current_span(
                "outbox.publish", context=parent_context, kind=SpanKind.PRODUCER
            ) as span:
                span.set_attribute("messaging.destination.name", TOPIC)
                span.set_attribute("messaging.message.id", payload["event_id"])
                meta = await producer.send_and_wait(
                    TOPIC,
                    value=row["payload"].encode(),
                    key=partition_key(payload),
                    headers=headers_for(payload),
                )
                span.set_attribute("messaging.kafka.partition", meta.partition)
        finally:
            correlation_id_var.reset(token)
            publish_duration.record((time.perf_counter() - started) * 1000)
        sent.append(row["event_id"])
        published_counter.add(1, {"event_type": payload["event_type"]})

        data = payload["data"]
        amount = sum(p["amount"] for p in data["postings"] if p["side"] == "debit")
        logger.info(
            "published event_type=%s transaction_id=%s amount_minor=%s partition=%s offset=%s",
            payload["event_type"], data["transaction_id"], amount,
            meta.partition, meta.offset,
        )

    if sent:
        await mark_published(conn, sent)
    return len(sent)


async def run(database_url: str | None = None) -> None:
    """Polls the outbox forever. Stop with Ctrl-C."""
    configure_telemetry("outbox-publisher")
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
    logger.info(
        "publisher connected brokers=%s topic=%s poll_seconds=%s",
        BOOTSTRAP_SERVERS, TOPIC, POLL_SECONDS,
    )

    try:
        while True:
            rows = await fetch_unpublished(conn, BATCH_SIZE)
            if rows:
                await publish_batch(conn, producer, rows)
            await observe_backlog(conn)
            await asyncio.sleep(POLL_SECONDS)
    finally:
        await producer.stop()
        await conn.close()
