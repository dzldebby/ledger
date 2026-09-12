from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from temporal_app.activities import (
        confirm_ledger_settlement,
        create_bank_payout,
        fail_ledger_settlement,
        prepare_settlement_batch,
        resolve_external_bank_account,
    )
    from temporal_app.models import (
        ConfirmSettlementInput,
        FailSettlementInput,
        PayoutInput,
        PrepareBatchInput,
        SettlementBatchInput,
        SettlementBatchResult,
        SettlementWorkflowInput,
        SettlementWorkflowResult,
    )

ACTIVITY_TIMEOUT = timedelta(seconds=20)
TRANSIENT_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2,
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=5,
)


@workflow.defn
class SettlementWorkflow:
    @workflow.run
    async def run(self, data: SettlementWorkflowInput) -> SettlementWorkflowResult:
        settlement = data.settlement
        if settlement.status in ("confirmed", "failed"):
            return SettlementWorkflowResult(
                settlement_id=settlement.settlement_id, status=settlement.status
            )

        payout = await workflow.execute_activity(
            create_bank_payout,
            PayoutInput(
                settlement=settlement,
                correlation_id=data.correlation_id,
                failures_before_success=data.payout_failures_before_success,
                simulate_rejection=data.simulate_bank_rejection,
                delay_seconds=data.payout_delay_seconds,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=TRANSIENT_RETRY_POLICY,
        )
        if payout.state != "confirmed" or not payout.payout_id:
            reason = payout.reason or f"external_bank_{payout.state}"
            await workflow.execute_activity(
                fail_ledger_settlement,
                FailSettlementInput(
                    settlement_id=settlement.settlement_id,
                    reason=reason,
                    correlation_id=data.correlation_id,
                ),
                start_to_close_timeout=ACTIVITY_TIMEOUT,
                retry_policy=TRANSIENT_RETRY_POLICY,
            )
            return SettlementWorkflowResult(
                settlement_id=settlement.settlement_id, status="failed"
            )

        status = await workflow.execute_activity(
            confirm_ledger_settlement,
            ConfirmSettlementInput(
                settlement_id=settlement.settlement_id,
                payout_id=payout.payout_id,
                correlation_id=data.correlation_id,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=TRANSIENT_RETRY_POLICY,
        )
        return SettlementWorkflowResult(
            settlement_id=settlement.settlement_id,
            status=status,
            payout_id=payout.payout_id,
        )


@workflow.defn
class SettlementBatchWorkflow:
    @workflow.run
    async def run(self, data: SettlementBatchInput) -> SettlementBatchResult:
        external_bank_account_id = await workflow.execute_activity(
            resolve_external_bank_account,
            data.correlation_id,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=TRANSIENT_RETRY_POLICY,
        )
        settlements = await workflow.execute_activity(
            prepare_settlement_batch,
            PrepareBatchInput(
                batch_id=data.batch_id,
                external_bank_account_id=external_bank_account_id,
                correlation_id=data.correlation_id,
            ),
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=TRANSIENT_RETRY_POLICY,
        )

        if data.workflow_delay_seconds:
            await workflow.sleep(timedelta(seconds=data.workflow_delay_seconds))

        results = []
        for settlement in settlements:
            result = await workflow.execute_child_workflow(
                SettlementWorkflow.run,
                SettlementWorkflowInput(
                    settlement=settlement,
                    correlation_id=data.correlation_id,
                    payout_failures_before_success=data.payout_failures_before_success,
                    simulate_bank_rejection=data.simulate_bank_rejection,
                    payout_delay_seconds=data.payout_delay_seconds,
                ),
                id=f"settlement-{settlement.settlement_id}",
            )
            results.append(result)

        return SettlementBatchResult(
            batch_id=data.batch_id,
            prepared=len(settlements),
            confirmed=sum(result.status == "confirmed" for result in results),
            failed=sum(result.status == "failed" for result in results),
            settlements=results,
        )
