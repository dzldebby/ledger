import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException, Request
from opentelemetry import metrics, trace

from app.telemetry import configure_telemetry, instrument_app

configure_telemetry("settlement-service")
logger = logging.getLogger(__name__)
tracer = trace.get_tracer("settlement-service")
run_counter = metrics.get_meter("settlement-service").create_counter("settlement.runs")

LEDGER_URL = os.getenv("LEDGER_URL", "http://app:8000").rstrip("/")
EXTERNAL_BANK_URL = os.getenv("EXTERNAL_BANK_URL", "http://external-bank:8003").rstrip("/")
LEDGER_API_KEY = os.getenv("LEDGER_API_KEY", "")
INTERVAL_SECONDS = int(os.getenv("SETTLEMENT_INTERVAL_SECONDS", "86400"))
RUN_ON_STARTUP = os.getenv("SETTLEMENT_RUN_ON_STARTUP", "false").lower() == "true"


def _headers(correlation_id: str) -> dict[str, str]:
    return {"X-API-Key": LEDGER_API_KEY, "X-Correlation-ID": correlation_id}


@tracer.start_as_current_span("settlement.resolve_bank_account")
async def _external_bank_account(client: httpx.AsyncClient, correlation_id: str) -> str:
    response = await client.get(f"{LEDGER_URL}/accounts", headers=_headers(correlation_id))
    response.raise_for_status()
    for account in response.json():
        if account["account_type"] == "external_bank":
            return account["account_id"]
    response = await client.post(
        f"{LEDGER_URL}/accounts",
        headers=_headers(correlation_id),
        json={"owner_id": "synthetic-external-bank", "account_type": "external_bank"},
    )
    response.raise_for_status()
    return response.json()["account_id"]


async def run_settlement_batch(correlation_id: str) -> dict:
    if not LEDGER_API_KEY:
        raise RuntimeError("LEDGER_API_KEY is not configured")
    with tracer.start_as_current_span("settlement.run_batch"):
        async with httpx.AsyncClient(timeout=10) as client:
            bank_account_id = await _external_bank_account(client, correlation_id)
            batch_id = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            response = await client.post(
                f"{LEDGER_URL}/settlements/prepare",
                headers=_headers(correlation_id),
                json={"batch_id": batch_id, "external_bank_account_id": bank_account_id},
            )
            response.raise_for_status()
            prepared = response.json()["settlements"]
            confirmed = 0
            failed = 0
            for settlement in prepared:
                if settlement["status"] == "confirmed":
                    confirmed += 1
                    continue
                if settlement["status"] == "failed":
                    failed += 1
                    continue
                payout = await client.post(
                    f"{EXTERNAL_BANK_URL}/payouts",
                    headers={"X-Correlation-ID": correlation_id},
                    json={
                        "settlement_id": settlement["settlement_id"],
                        "account_id": settlement["account_id"],
                        "amount_minor": settlement["amount_minor"],
                        "correlation_id": correlation_id,
                    },
                )
                if payout.is_success and payout.json()["state"] == "confirmed":
                    result = await client.post(
                        f"{LEDGER_URL}/settlements/{settlement['settlement_id']}/confirm",
                        headers=_headers(correlation_id),
                        json={"external_reference": payout.json()["payout_id"]},
                    )
                    result.raise_for_status()
                    confirmed += 1
                else:
                    failure = await client.post(
                        f"{LEDGER_URL}/settlements/{settlement['settlement_id']}/fail",
                        headers=_headers(correlation_id),
                        json={"reason": "external_bank_rejected"},
                    )
                    failure.raise_for_status()
                    failed += 1
            run_counter.add(1, {"outcome": "complete"})
            return {
                "batch_id": batch_id,
                "prepared": len(prepared),
                "confirmed": confirmed,
                "failed": failed,
                "correlation_id": correlation_id,
            }


async def scheduler() -> None:
    if RUN_ON_STARTUP:
        try:
            await run_settlement_batch(str(uuid.uuid4()))
        except Exception:
            logger.exception("startup settlement batch failed")
    while True:
        await asyncio.sleep(INTERVAL_SECONDS)
        try:
            await run_settlement_batch(str(uuid.uuid4()))
        except Exception:
            logger.exception("scheduled settlement batch failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(scheduler())
    yield
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


app = FastAPI(title="Settlement Orchestrator", lifespan=lifespan)
instrument_app(app)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/settlements/run")
async def run_now(request: Request):
    try:
        return await run_settlement_batch(request.state.correlation_id)
    except (httpx.HTTPError, RuntimeError) as exc:
        run_counter.add(1, {"outcome": "failed"})
        raise HTTPException(status_code=503, detail=str(exc))
