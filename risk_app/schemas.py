from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RiskDecisionRequest(BaseModel):
    client_scope: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=255)
    transaction_type: Literal["deposit", "transfer"]
    amount_minor: int = Field(gt=0)
    owner_ids: list[str] = Field(min_length=1)
    correlation_id: str = Field(min_length=1, max_length=255)


class RiskDecisionResponse(BaseModel):
    decision_id: str
    state: Literal["approved", "declined", "review"]
    reasons: list[str]
    expires_at: datetime
    correlation_id: str
