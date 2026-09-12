import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from compliance_app.service import process_event


class ComplianceConnection:
    def __init__(self):
        self.fetchval = AsyncMock(side_effect=["event-id", None])
        self.execute = AsyncMock()

    @asynccontextmanager
    async def transaction(self):
        yield


@pytest.mark.asyncio
@pytest.mark.parametrize("amount, expected_alerts", [(1, 1), (2, 0), (100, 0)])
async def test_single_minor_unit_alert_and_duplicate_delivery(amount, expected_alerts):
    connection = ComplianceConnection()
    event = {
        "event_id": "event-id",
        "event_type": "transaction.deposit",
        "correlation_id": "request-id",
        "occurred_at": "2026-09-08T12:00:00+00:00",
        "data": {
            "transaction_id": "transaction-id",
            "postings": [
                {"account_id": "source-id", "side": "debit", "amount": amount},
                {"account_id": "target-id", "side": "credit", "amount": amount},
            ],
        },
    }

    assert await process_event(connection, event) is True
    assert await process_event(connection, event) is False
    alerts = [
        call.args for call in connection.execute.await_args_list
        if "INSERT INTO compliance_alerts" in call.args[0]
    ]
    assert len(alerts) == expected_alerts
    if expected_alerts:
        assert alerts[0][3:5] == ("single_minor_unit_transaction", "low")
        assert json.loads(alerts[0][5]) == {"amount_minor": 1}
