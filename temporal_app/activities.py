import os

import httpx
from temporalio import activity

from temporal_app.models import (
    ConfirmSettlementInput,
    FailSettlementInput,
    PayoutInput,
    PayoutResult,
    PrepareBatchInput,
    SettlementRecord,
)

LEDGER_URL = os.getenv("LEDGER_URL", "http://app:8000").rstrip("/")
EXTERNAL_BANK_URL = os.getenv("EXTERNAL_BANK_URL", "http://external-bank:8003").rstrip("/")
LEDGER_API_KEY = os.getenv("LEDGER_API_KEY", "")


def _ledger_headers(correlation_id: str) -> dict[str, str]:
    return {"X-API-Key": LEDGER_API_KEY, "X-Correlation-ID": correlation_id}


@activity.defn
async def resolve_external_bank_account(correlation_id: str) -> str:
    activity.logger.info("resolving external bank ledger account")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(
            f"{LEDGER_URL}/accounts", headers=_ledger_headers(correlation_id)
        )
        response.raise_for_status()
        for account in response.json():
            if account["account_type"] == "external_bank":
                return account["account_id"]
        response = await client.post(
            f"{LEDGER_URL}/accounts",
            headers=_ledger_headers(correlation_id),
            json={
                "owner_id": "synthetic-external-bank",
                "account_type": "external_bank",
            },
        )
        response.raise_for_status()
        return response.json()["account_id"]


@activity.defn
async def prepare_settlement_batch(data: PrepareBatchInput) -> list[SettlementRecord]:
    activity.logger.info("preparing settlement batch %s", data.batch_id)
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{LEDGER_URL}/settlements/prepare",
            headers=_ledger_headers(data.correlation_id),
            json={
                "batch_id": data.batch_id,
                "external_bank_account_id": data.external_bank_account_id,
            },
        )
        response.raise_for_status()
        return [
            SettlementRecord(
                settlement_id=item["settlement_id"],
                account_id=item["account_id"],
                amount_minor=item["amount_minor"],
                status=item["status"],
            )
            for item in response.json()["settlements"]
        ]


@activity.defn
async def create_bank_payout(data: PayoutInput) -> PayoutResult:
    settlement = data.settlement
    activity.logger.info("requesting payout for settlement %s", settlement.settlement_id)
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{EXTERNAL_BANK_URL}/payouts",
            headers={"X-Correlation-ID": data.correlation_id},
            json={
                "settlement_id": settlement.settlement_id,
                "account_id": settlement.account_id,
                "amount_minor": settlement.amount_minor,
                "correlation_id": data.correlation_id,
                "failures_before_success": data.failures_before_success,
                "simulate_rejection": data.simulate_rejection,
                "delay_seconds": data.delay_seconds,
            },
        )
        if 400 <= response.status_code < 500:
            return PayoutResult(
                payout_id=None,
                state="rejected",
                reason=f"external_bank_http_{response.status_code}",
            )
        response.raise_for_status()
        payload = response.json()
        return PayoutResult(
            payout_id=payload.get("payout_id"),
            state=payload["state"],
            reason=payload.get("reason"),
        )


@activity.defn
async def confirm_ledger_settlement(data: ConfirmSettlementInput) -> str:
    activity.logger.info("confirming ledger settlement %s", data.settlement_id)
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{LEDGER_URL}/settlements/{data.settlement_id}/confirm",
            headers=_ledger_headers(data.correlation_id),
            json={"external_reference": data.payout_id},
        )
        response.raise_for_status()
        return response.json()["settlement"]["status"]


@activity.defn
async def fail_ledger_settlement(data: FailSettlementInput) -> str:
    activity.logger.info("failing ledger settlement %s", data.settlement_id)
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"{LEDGER_URL}/settlements/{data.settlement_id}/fail",
            headers=_ledger_headers(data.correlation_id),
            json={"reason": data.reason},
        )
        response.raise_for_status()
        return response.json()["status"]
