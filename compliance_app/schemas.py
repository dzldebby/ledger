from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ComplianceAlert(BaseModel):
    alert_id: str
    event_id: str
    transaction_id: str
    rule_code: str
    severity: str
    status: str
    details: dict[str, Any]
    correlation_id: str
    created_at: datetime


class DeadLetterRecord(BaseModel):
    delivery_key: str
    event_id: str | None
    attempts: int
    last_error: str
    dead_lettered_at: datetime
