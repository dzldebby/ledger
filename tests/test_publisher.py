"""Publisher behaviour that does not need a broker running."""
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.services.publisher import headers_for, partition_key, publish_batch

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "contracts" / "events"


def fixture(name):
    return json.loads((FIXTURE_DIR / f"transaction.{name}.v1.json").read_text())


class TestPartitionKey:
    """Kafka orders messages only within a partition, and the key chooses the
    partition - so the key is where ordering is decided."""

    def test_a_reversal_lands_on_its_originals_partition(self):
        """The whole point. The reversal fixture reverses the transfer
        fixture, so both must key to the transfer's transaction_id or Kafka
        is free to deliver the reversal first."""
        transfer, reversal = fixture("transfer"), fixture("reversal")

        assert partition_key(reversal) == partition_key(transfer)

    def test_the_reversal_does_not_key_on_its_own_id(self):
        """A reversal has its own transaction_id. Keying on it would scatter
        the pair across partitions, which is the bug this design avoids."""
        reversal = fixture("reversal")
        own_id = reversal["data"]["transaction_id"].encode()

        assert partition_key(reversal) != own_id

    @pytest.mark.parametrize("name", ["deposit", "transfer", "settlement"])
    def test_a_non_reversal_keys_on_its_own_transaction(self, name):
        event = fixture(name)
        assert partition_key(event) == event["data"]["transaction_id"].encode()

    def test_key_is_bytes(self):
        """aiokafka will not serialize a str key for us."""
        assert isinstance(partition_key(fixture("deposit")), bytes)


class TestHeaders:
    """Envelope fields lifted out of the value so a router can dispatch
    without deserializing the body."""

    @pytest.mark.parametrize("name", ["deposit", "transfer", "reversal", "settlement"])
    def test_carries_the_routing_fields(self, name):
        headers = dict(headers_for(fixture(name)))
        assert set(headers) == {
            "event_id", "event_type", "schema_version", "correlation_id"
        }

    def test_values_match_the_payload(self):
        event = fixture("reversal")
        headers = dict(headers_for(event))

        assert headers["event_id"].decode() == event["event_id"]
        assert headers["event_type"].decode() == "transaction.reversal"
        assert headers["schema_version"].decode() == "1"
        assert headers["correlation_id"].decode() == "corr-reversal-001"

    def test_values_are_bytes(self):
        assert all(isinstance(v, bytes) for _, v in headers_for(fixture("deposit")))


class RecordingConnection:
    def __init__(self):
        self.executions = []

    async def execute(self, query, event_ids):
        self.executions.append((query, event_ids))


class RecordingProducer:
    def __init__(self, fail_on_send=None):
        self.fail_on_send = fail_on_send
        self.messages = []

    async def send_and_wait(self, topic, **message):
        self.messages.append((topic, message))
        if len(self.messages) == self.fail_on_send:
            raise RuntimeError("broker unavailable")
        return SimpleNamespace(partition=0, offset=len(self.messages) - 1)


def outbox_row(name, event_id):
    return {
        "event_id": UUID(event_id),
        "payload": json.dumps(fixture(name)),
    }


class TestPublishBatch:
    @pytest.mark.asyncio
    async def test_sends_every_event_then_marks_the_batch_published(self):
        rows = [
            outbox_row("deposit", "0f6a1c3e-9b2d-4a71-8f3c-1d2e5a7b9c04"),
            outbox_row("transfer", "1a7b2d4f-0c3e-5b82-9a4d-2e3f6b8c0d15"),
        ]
        conn = RecordingConnection()
        producer = RecordingProducer()

        count = await publish_batch(conn, producer, rows)

        assert count == 2
        assert [topic for topic, _ in producer.messages] == ["ledger.events"] * 2
        assert len(conn.executions) == 1
        assert conn.executions[0][1] == [row["event_id"] for row in rows]

    @pytest.mark.asyncio
    async def test_a_send_failure_does_not_mark_any_event_published(self):
        rows = [
            outbox_row("deposit", "0f6a1c3e-9b2d-4a71-8f3c-1d2e5a7b9c04"),
            outbox_row("transfer", "1a7b2d4f-0c3e-5b82-9a4d-2e3f6b8c0d15"),
        ]
        conn = RecordingConnection()
        producer = RecordingProducer(fail_on_send=2)

        with pytest.raises(RuntimeError, match="broker unavailable"):
            await publish_batch(conn, producer, rows)

        assert len(producer.messages) == 2
        assert conn.executions == []
