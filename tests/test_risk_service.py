import pytest

from risk_app.schemas import RiskDecisionRequest
from risk_app.service import _evaluate, _request_hash


class FakeConnection:
    def __init__(self, recent=0):
        self.recent = recent

    async def fetchval(self, query, *args):
        return self.recent

    async def execute(self, query, *args):
        return "SELECT 1"


def request(**overrides):
    values = {
        "client_scope": "client-1",
        "idempotency_key": "idem-1",
        "transaction_type": "transfer",
        "amount_minor": 1000,
        "owner_ids": ["alice", "bob"],
        "correlation_id": "corr-1",
    }
    values.update(overrides)
    return RiskDecisionRequest(**values)


@pytest.mark.asyncio
async def test_normal_transaction_is_approved():
    assert await _evaluate(FakeConnection(), request()) == ("approved", [])


@pytest.mark.asyncio
async def test_blocked_owner_is_declined():
    state, reasons = await _evaluate(
        FakeConnection(), request(owner_ids=["blocked-alice", "bob"])
    )
    assert state == "declined"
    assert reasons == ["blocked_owner"]


@pytest.mark.asyncio
async def test_large_transaction_requires_review():
    state, reasons = await _evaluate(
        FakeConnection(), request(amount_minor=500000)
    )
    assert state == "review"
    assert reasons == ["large_transaction"]


@pytest.mark.asyncio
async def test_velocity_limit_requires_review():
    state, reasons = await _evaluate(FakeConnection(recent=5), request())
    assert state == "review"
    assert reasons == ["velocity_limit_reached"]


def test_retry_hash_ignores_correlation_id():
    first = _request_hash(request(correlation_id="corr-1"))
    retry = _request_hash(request(correlation_id="corr-2"))
    assert first == retry
