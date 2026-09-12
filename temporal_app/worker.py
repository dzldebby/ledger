import asyncio
import os

from temporalio.worker import Worker

from app.telemetry import configure_telemetry
from temporal_app.activities import (
    confirm_ledger_settlement,
    create_bank_payout,
    fail_ledger_settlement,
    prepare_settlement_batch,
    resolve_external_bank_account,
)
from temporal_app.client import connect_temporal
from temporal_app.workflows import SettlementBatchWorkflow, SettlementWorkflow

TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "ledger-settlements")


async def main() -> None:
    configure_telemetry("temporal-settlement-worker")
    client = await connect_temporal()
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[SettlementBatchWorkflow, SettlementWorkflow],
        activities=[
            resolve_external_bank_account,
            prepare_settlement_batch,
            create_bank_payout,
            confirm_ledger_settlement,
            fail_ledger_settlement,
        ],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
