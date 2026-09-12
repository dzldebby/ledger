import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

import asyncpg
from fastapi import HTTPException
from opentelemetry import metrics, trace

from risk_app.schemas import RiskDecisionRequest, RiskDecisionResponse

REVIEW_AMOUNT_MINOR = int(os.getenv("RISK_REVIEW_AMOUNT_MINOR", "500000"))
DECLINE_AMOUNT_MINOR = int(os.getenv("RISK_DECLINE_AMOUNT_MINOR", "1000000"))
VELOCITY_LIMIT = int(os.getenv("RISK_VELOCITY_LIMIT_PER_MINUTE", "5"))
DECISION_TTL_SECONDS = int(os.getenv("RISK_DECISION_TTL_SECONDS", "300"))

tracer = trace.get_tracer("risk-service")
decision_counter = metrics.get_meter("risk-service").create_counter("risk.decisions")


def _request_hash(data: RiskDecisionRequest) -> str:
    business_request = {
        "transaction_type": data.transaction_type,
        "amount_minor": data.amount_minor,
        "owner_ids": data.owner_ids,
    }
    encoded = json.dumps(business_request, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


@tracer.start_as_current_span("risk.rules.evaluate")
async def _evaluate(conn: asyncpg.Connection, data: RiskDecisionRequest) -> tuple[str, list[str]]:
    if any(owner.lower().startswith("blocked-") for owner in data.owner_ids):
        return "declined", ["blocked_owner"]
    if data.amount_minor >= DECLINE_AMOUNT_MINOR:
        return "declined", ["amount_limit_exceeded"]

    recent = await conn.fetchval("""
        SELECT count(*) FROM risk_decisions
        WHERE client_scope = $1 AND created_at >= clock_timestamp() - interval '1 minute'
    """, data.client_scope)
    if recent >= VELOCITY_LIMIT:
        return "review", ["velocity_limit_reached"]
    if data.amount_minor >= REVIEW_AMOUNT_MINOR:
        return "review", ["large_transaction"]
    return "approved", []


def _response(row) -> RiskDecisionResponse:
    reasons = row["reasons"]
    if isinstance(reasons, str):
        reasons = json.loads(reasons)
    return RiskDecisionResponse(
        decision_id=str(row["decision_id"]),
        state=row["state"],
        reasons=reasons,
        expires_at=row["expires_at"],
        correlation_id=row["correlation_id"],
    )


async def create_decision(conn: asyncpg.Connection, data: RiskDecisionRequest) -> RiskDecisionResponse:
    request_hash = _request_hash(data)
    with tracer.start_as_current_span("risk.evaluate") as span:
        span.set_attribute("risk.transaction_type", data.transaction_type)
        span.set_attribute("risk.amount_minor", data.amount_minor)
        async with conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended($1 || ':' || $2, 0))",
                data.client_scope,
                data.idempotency_key,
            )
            existing = await conn.fetchrow("""
                SELECT * FROM risk_decisions
                WHERE client_scope = $1 AND idempotency_key = $2
                FOR UPDATE
            """, data.client_scope, data.idempotency_key)
            if existing:
                if existing["request_hash"] != request_hash:
                    raise HTTPException(
                        status_code=409,
                        detail="Idempotency key already used with a different request",
                    )
                decision_counter.add(1, {"state": existing["state"], "replayed": True})
                return _response(existing)

            state, reasons = await _evaluate(conn, data)
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=DECISION_TTL_SECONDS)
            row = await conn.fetchrow("""
                INSERT INTO risk_decisions (
                    client_scope, idempotency_key, request_hash, transaction_type,
                    amount_minor, owner_ids, state, reasons, correlation_id, expires_at
                ) VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8::jsonb, $9, $10)
                RETURNING *
            """, data.client_scope, data.idempotency_key, request_hash,
                 data.transaction_type, data.amount_minor, json.dumps(data.owner_ids),
                 state, json.dumps(reasons), data.correlation_id, expires_at)
            span.set_attribute("risk.decision", state)
            decision_counter.add(1, {"state": state, "replayed": False})
            return _response(row)
