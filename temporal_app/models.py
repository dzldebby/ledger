from dataclasses import dataclass, field


@dataclass
class SettlementRecord:
    settlement_id: str
    account_id: str
    amount_minor: int
    status: str


@dataclass
class SettlementWorkflowInput:
    settlement: SettlementRecord
    correlation_id: str
    payout_failures_before_success: int = 0
    simulate_bank_rejection: bool = False
    payout_delay_seconds: int = 0


@dataclass
class SettlementWorkflowResult:
    settlement_id: str
    status: str
    payout_id: str | None = None


@dataclass
class SettlementBatchInput:
    batch_id: str
    correlation_id: str
    payout_failures_before_success: int = 0
    simulate_bank_rejection: bool = False
    payout_delay_seconds: int = 0
    workflow_delay_seconds: int = 0


@dataclass
class SettlementBatchResult:
    batch_id: str
    prepared: int
    confirmed: int
    failed: int
    settlements: list[SettlementWorkflowResult] = field(default_factory=list)


@dataclass
class PrepareBatchInput:
    batch_id: str
    external_bank_account_id: str
    correlation_id: str


@dataclass
class PayoutInput:
    settlement: SettlementRecord
    correlation_id: str
    failures_before_success: int = 0
    simulate_rejection: bool = False
    delay_seconds: int = 0


@dataclass
class PayoutResult:
    payout_id: str | None
    state: str
    reason: str | None = None


@dataclass
class ConfirmSettlementInput:
    settlement_id: str
    payout_id: str
    correlation_id: str


@dataclass
class FailSettlementInput:
    settlement_id: str
    reason: str
    correlation_id: str
