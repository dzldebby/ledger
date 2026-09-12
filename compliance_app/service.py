import json
import os
from datetime import datetime

import asyncpg
from opentelemetry import metrics, trace
from opentelemetry.propagate import extract
from opentelemetry.trace import SpanKind

from compliance_app.schemas import ComplianceAlert, DeadLetterRecord

LARGE_TRANSACTION_MINOR = int(os.getenv("COMPLIANCE_LARGE_TRANSACTION_MINOR", "500000"))
REPEATED_TRANSFER_COUNT = int(os.getenv("COMPLIANCE_REPEATED_TRANSFER_COUNT", "3"))
REPEATED_TRANSFER_WINDOW_MINUTES = int(
    os.getenv("COMPLIANCE_REPEATED_TRANSFER_WINDOW_MINUTES", "5")
)

tracer = trace.get_tracer("compliance-service")
meter = metrics.get_meter("compliance-service")
event_counter = meter.create_counter("compliance.events")
alert_counter = meter.create_counter("compliance.alerts")


def transaction_amount(event: dict) -> int:
    return sum(
        posting["amount"]
        for posting in event["data"]["postings"]
        if posting["side"] == "debit"
    )


def debit_account(event: dict) -> str | None:
    for posting in event["data"]["postings"]:
        if posting["side"] == "debit":
            return posting["account_id"]
    return None


async def process_event(conn: asyncpg.Connection, event: dict) -> bool:
    carrier = {}
    if event.get("traceparent"):
        carrier["traceparent"] = event["traceparent"]
    context = extract(carrier)
    with tracer.start_as_current_span(
        "compliance.evaluate", context=context, kind=SpanKind.CONSUMER
    ) as span:
        event_id = event["event_id"]
        data = event["data"]
        amount = transaction_amount(event)
        correlation_id = event["correlation_id"]
        span.set_attribute("messaging.message.id", event_id)
        span.set_attribute("ledger.transaction_id", data["transaction_id"])
        span.set_attribute("correlation_id", correlation_id)
        span.set_attribute("ledger.amount_minor", amount)

        async with conn.transaction():
            inserted = await conn.fetchval("""
                INSERT INTO processed_events (
                    event_id, event_type, transaction_id, correlation_id, payload
                ) VALUES ($1::uuid, $2, $3::uuid, $4, $5::jsonb)
                ON CONFLICT (event_id) DO NOTHING
                RETURNING event_id
            """, event_id, event["event_type"], data["transaction_id"],
                 correlation_id, json.dumps(event))
            if inserted is None:
                span.add_event("compliance.duplicate_skipped")
                event_counter.add(1, {"outcome": "duplicate"})
                return False

            account_id = debit_account(event)
            occurred_at = datetime.fromisoformat(event["occurred_at"])
            if account_id:
                await conn.execute("""
                    INSERT INTO compliance_activity (
                        event_id, account_id, event_type, amount_minor, occurred_at
                    ) VALUES ($1::uuid, $2::uuid, $3, $4, $5)
                """, event_id, account_id, event["event_type"], amount, occurred_at)

            alerts = []
            if amount == 1:
                alerts.append((
                    "single_minor_unit_transaction",
                    "low",
                    {"amount_minor": amount},
                ))

            if amount >= LARGE_TRANSACTION_MINOR:
                alerts.append((
                    "large_transaction",
                    "high",
                    {"amount_minor": amount, "threshold_minor": LARGE_TRANSACTION_MINOR},
                ))

            if event["event_type"] == "transaction.transfer" and account_id:
                recent_count = await conn.fetchval("""
                    SELECT count(*) FROM compliance_activity
                    WHERE account_id = $1::uuid
                      AND event_type = 'transaction.transfer'
                      AND occurred_at >= $2::timestamptz - ($3::integer * interval '1 minute')
                      AND occurred_at <= $2::timestamptz + ($3::integer * interval '1 minute')
                """, account_id, occurred_at, REPEATED_TRANSFER_WINDOW_MINUTES)
                if recent_count >= REPEATED_TRANSFER_COUNT:
                    alerts.append((
                        "repeated_transfers",
                        "medium",
                        {
                            "count": recent_count,
                            "window_minutes": REPEATED_TRANSFER_WINDOW_MINUTES,
                        },
                    ))

            for rule_code, severity, details in alerts:
                span.add_event("compliance.alert", {"rule": rule_code, "severity": severity})
                await conn.execute("""
                    INSERT INTO compliance_alerts (
                        event_id, transaction_id, rule_code, severity, details,
                        correlation_id
                    ) VALUES ($1::uuid, $2::uuid, $3, $4, $5::jsonb, $6)
                    ON CONFLICT (event_id, rule_code) DO NOTHING
                """, event_id, data["transaction_id"], rule_code, severity,
                     json.dumps(details), correlation_id)
                alert_counter.add(1, {"rule": rule_code, "severity": severity})

        event_counter.add(1, {"outcome": "processed"})
        return True


async def list_alerts(conn: asyncpg.Connection, limit: int = 100) -> list[ComplianceAlert]:
    rows = await conn.fetch("""
        SELECT * FROM compliance_alerts
        ORDER BY created_at DESC, alert_id DESC LIMIT $1
    """, limit)
    alerts = []
    for row in rows:
        details = row["details"]
        if isinstance(details, str):
            details = json.loads(details)
        alerts.append(ComplianceAlert(
            alert_id=str(row["alert_id"]),
            event_id=str(row["event_id"]),
            transaction_id=str(row["transaction_id"]),
            rule_code=row["rule_code"],
            severity=row["severity"],
            status=row["status"],
            details=details,
            correlation_id=row["correlation_id"],
            created_at=row["created_at"],
        ))
    return alerts


async def list_dead_letters(
    conn: asyncpg.Connection, limit: int = 100
) -> list[DeadLetterRecord]:
    rows = await conn.fetch("""
        SELECT delivery_key, event_id, attempts, last_error, dead_lettered_at
        FROM event_failures
        WHERE dead_lettered_at IS NOT NULL
        ORDER BY dead_lettered_at DESC LIMIT $1
    """, limit)
    return [DeadLetterRecord(**dict(row)) for row in rows]
