import asyncio
import json
import logging
import os

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from opentelemetry import metrics, trace
from opentelemetry.propagate import extract, inject
from opentelemetry.trace import SpanKind, StatusCode

from app.telemetry import correlation_id_var
from compliance_app import database
from compliance_app.service import process_event

logger = logging.getLogger(__name__)
TOPIC = os.getenv("KAFKA_TOPIC", "ledger.events")
DLQ_TOPIC = os.getenv("KAFKA_DLQ_TOPIC", "ledger.events.dlq")
BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
MAX_RETRIES = int(os.getenv("COMPLIANCE_MAX_RETRIES", "3"))

tracer = trace.get_tracer("compliance-service")
failure_counter = metrics.get_meter("compliance-service").create_counter(
    "compliance.consumer.failures"
)


async def _record_failure(conn, delivery_key, event_id, payload, error, attempts, dead_lettered):
    await conn.execute("""
        INSERT INTO event_failures (
            delivery_key, event_id, attempts, last_error, payload, dead_lettered_at
        ) VALUES ($1, $2, $3, $4, $5,
                  CASE WHEN $6 THEN clock_timestamp() ELSE NULL END)
        ON CONFLICT (delivery_key) DO UPDATE SET
            attempts = EXCLUDED.attempts,
            last_error = EXCLUDED.last_error,
            dead_lettered_at = EXCLUDED.dead_lettered_at,
            updated_at = clock_timestamp()
    """, delivery_key, event_id, attempts, str(error)[:1000],
         payload.decode(errors="replace"), dead_lettered)


async def consume_forever() -> None:
    while True:
        consumer = AIOKafkaConsumer(
            TOPIC,
            bootstrap_servers=BOOTSTRAP_SERVERS,
            group_id="compliance-service",
            auto_offset_reset="earliest",
            enable_auto_commit=False,
        )
        producer = AIOKafkaProducer(
            bootstrap_servers=BOOTSTRAP_SERVERS,
            acks="all",
            enable_idempotence=True,
        )
        consumer_started = False
        producer_started = False
        try:
            await consumer.start()
            consumer_started = True
            await producer.start()
            producer_started = True
            logger.info("compliance consumer connected")
            async for message in consumer:
                delivery_key = f"{message.topic}:{message.partition}:{message.offset}"
                event_id = None
                for attempt in range(1, MAX_RETRIES + 1):
                    try:
                        event = json.loads(message.value)
                        message_headers = dict(message.headers or [])
                        if message_headers.get("traceparent"):
                            event["traceparent"] = message_headers["traceparent"].decode()
                        event_id = event.get("event_id")
                        token = correlation_id_var.set(event.get("correlation_id"))
                        try:
                            async with database.pool.acquire() as conn:
                                await process_event(conn, event)
                        finally:
                            correlation_id_var.reset(token)
                        await consumer.commit()
                        break
                    except Exception as exc:
                        dead_lettered = attempt == MAX_RETRIES
                        async with database.pool.acquire() as conn:
                            await _record_failure(
                                conn, delivery_key, event_id, message.value, exc,
                                attempt, dead_lettered,
                            )
                        failure_counter.add(1, {"dead_lettered": dead_lettered})
                        if dead_lettered:
                            carrier = {
                                key: value.decode(errors="replace")
                                for key, value in (message.headers or [])
                                if value is not None and key in ("traceparent", "tracestate")
                            }
                            with tracer.start_as_current_span(
                                "compliance.dead_letter", context=extract(carrier),
                                kind=SpanKind.PRODUCER,
                            ) as span:
                                span.set_attribute("messaging.destination.name", DLQ_TOPIC)
                                span.set_attribute("messaging.retry.count", attempt)
                                span.set_status(StatusCode.ERROR, "consumer retries exhausted")
                                span.add_event("compliance.retries_exhausted")
                                inject(carrier)
                                await producer.send_and_wait(
                                    DLQ_TOPIC,
                                    value=message.value,
                                    key=message.key,
                                    headers=[
                                        (key, value) for key, value in (message.headers or [])
                                        if key not in ("traceparent", "tracestate")
                                    ] + [(key, value.encode()) for key, value in carrier.items()] + [
                                        ("failure", str(exc)[:500].encode())
                                    ],
                                )
                            await consumer.commit()
                        else:
                            await asyncio.sleep(2 ** (attempt - 1))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("compliance consumer disconnected; retrying")
            await asyncio.sleep(3)
        finally:
            if consumer_started:
                await consumer.stop()
            if producer_started:
                await producer.stop()
