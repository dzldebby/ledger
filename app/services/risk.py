import logging
import os

import asyncpg
import httpx
from opentelemetry import metrics, trace

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("ledger-api")
risk_call_counter = metrics.get_meter("ledger-api").create_counter("ledger.risk.calls")


class RiskDeclinedError(Exception):
    def __init__(self, reasons: list[str]):
        self.reasons = reasons


class RiskReviewError(Exception):
    def __init__(self, reasons: list[str]):
        self.reasons = reasons


class RiskUnavailableError(Exception):
    pass


async def evaluate_risk(
    conn: asyncpg.Connection,
    *,
    transaction_type: str,
    account_ids: list[str],
    amount_minor: int,
    client_scope: str,
    idempotency_key: str,
    correlation_id: str,
) -> str | None:
    service_url = os.getenv("RISK_SERVICE_URL")
    if not service_url:
        return None

    rows = await conn.fetch("""
        SELECT owner_id FROM accounts WHERE account_id = ANY($1::uuid[])
    """, account_ids)
    if len(rows) != len(set(account_ids)):
        return None

    payload = {
        "client_scope": client_scope,
        "idempotency_key": idempotency_key,
        "transaction_type": transaction_type,
        "amount_minor": amount_minor,
        "owner_ids": sorted(row["owner_id"] for row in rows),
        "correlation_id": correlation_id,
    }
    timeout = float(os.getenv("RISK_TIMEOUT_SECONDS", "2"))
    with tracer.start_as_current_span("ledger.risk_decision") as span:
        span.set_attribute("risk.transaction_type", transaction_type)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{service_url.rstrip('/')}/risk-decisions",
                    json=payload,
                    headers={"X-Correlation-ID": correlation_id},
                )
            response.raise_for_status()
        except (httpx.HTTPError, ValueError) as exc:
            risk_call_counter.add(1, {"outcome": "unavailable"})
            logger.error("risk decision unavailable: %s", exc)
            raise RiskUnavailableError() from exc

        decision = response.json()
        state = decision["state"]
        reasons = decision.get("reasons", [])
        span.set_attribute("risk.decision", state)
        span.set_attribute("risk.decision_id", decision["decision_id"])
        span.add_event("risk.decision", {"risk.state": state, "risk.reasons": reasons})
        risk_call_counter.add(1, {"outcome": state})
        if state == "declined":
            raise RiskDeclinedError(reasons)
        if state == "review":
            raise RiskReviewError(reasons)
        return decision["decision_id"]
