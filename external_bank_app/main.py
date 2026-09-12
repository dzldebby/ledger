import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from opentelemetry import trace

from app.telemetry import configure_telemetry, instrument_app

configure_telemetry("external-bank")


class PayoutRequest(BaseModel):
    settlement_id: str
    account_id: str
    amount_minor: int = Field(gt=0)
    correlation_id: str
    failures_before_success: int = Field(default=0, ge=0, le=4)
    simulate_rejection: bool = False
    delay_seconds: int = Field(default=0, ge=0, le=15)


class PayoutResponse(BaseModel):
    payout_id: str
    state: str
    confirmed_at: datetime


payouts: dict[str, PayoutResponse] = {}
payout_attempts: dict[str, int] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title="Synthetic External Bank", lifespan=lifespan)
instrument_app(app)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/payouts", response_model=PayoutResponse, status_code=201)
@trace.get_tracer("external-bank").start_as_current_span("bank.payout")
async def create_payout(data: PayoutRequest):
    span = trace.get_current_span()
    span.set_attribute("settlement.id", data.settlement_id)
    span.set_attribute("ledger.amount_minor", data.amount_minor)
    existing = payouts.get(data.settlement_id)
    if existing:
        span.add_event("bank.payout.replayed")
        return existing
    payout_attempts[data.settlement_id] = payout_attempts.get(data.settlement_id, 0) + 1
    attempt = payout_attempts[data.settlement_id]
    span.set_attribute("bank.payout.attempt", attempt)
    if data.delay_seconds:
        await asyncio.sleep(data.delay_seconds)
    if attempt <= data.failures_before_success:
        span.add_event("bank.payout.transient_failure")
        raise HTTPException(status_code=503, detail="simulated temporary bank outage")
    payout = PayoutResponse(
        payout_id=str(uuid.uuid4()),
        state="rejected" if data.simulate_rejection else "confirmed",
        confirmed_at=datetime.now(timezone.utc),
    )
    payouts[data.settlement_id] = payout
    return payout
