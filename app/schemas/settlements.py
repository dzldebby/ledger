from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.transactions import TransactionResponse


class SettlementPrepare(BaseModel):
    batch_id: str = Field(min_length=1, max_length=128)
    external_bank_account_id: str


class SettlementResponse(BaseModel):
    settlement_id: str
    account_id: str
    external_bank_account_id: str
    amount_minor: int
    status: Literal["pending", "confirmed", "failed"]
    external_reference: str | None
    correlation_id: str
    effective_at: datetime
    confirmed_at: datetime | None
    transaction_id: str | None


class SettlementBatchResponse(BaseModel):
    settlements: list[SettlementResponse]
    count: int


class SettlementConfirmation(BaseModel):
    external_reference: str = Field(min_length=1, max_length=255)


class SettlementConfirmationResponse(BaseModel):
    settlement: SettlementResponse
    transaction: TransactionResponse


class SettlementFailure(BaseModel):
    reason: str = Field(min_length=1, max_length=255)
