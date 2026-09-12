from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AccountCreate(BaseModel):
    owner_id: str
    account_type: Literal["cash", "personal", "customer", "business", "external_bank"]
    currency: str = Field(default="USD", min_length=3, max_length=3, pattern=r"^[A-Za-z]{3}$")


class AccountResponse(BaseModel):
    account_id: str
    owner_id: str
    account_type: str
    currency: str
    status: str


class BalanceResponse(BaseModel):
    account_id: str
    balance_minor: int
    available_balance_minor: int


class AccountPosting(BaseModel):
    side: str
    amount_minor: int


class AccountTransaction(BaseModel):
    transaction_id: str
    type: str
    state: str
    reversal_of_id: str | None
    recorded_at: datetime
    posting: AccountPosting


class AccountHistoryResponse(BaseModel):
    account_id: str
    transactions: list[AccountTransaction]
    count: int
