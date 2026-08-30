"""Publisher behaviour that does not need a broker running.

The send/mark ordering is the publisher's other correctness property, but
asserting it needs a real Kafka and a crash injected between the two steps.
It is argued for in publisher.publish_batch's docstring instead; these tests
cover the part that is pure logic.
"""
import json
from pathlib import Path

import pytest

from app.services.publisher import headers_for, partition_key

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

    @pytest.mark.parametrize("name", ["deposit", "transfer"])
    def test_a_non_reversal_keys_on_its_own_transaction(self, name):
        event = fixture(name)
        assert partition_key(event) == event["data"]["transaction_id"].encode()

    def test_key_is_bytes(self):
        """aiokafka will not serialize a str key for us."""
        assert isinstance(partition_key(fixture("deposit")), bytes)


class TestHeaders:
    """Envelope fields lifted out of the value so a router can dispatch
    without deserializing the body."""

    @pytest.mark.parametrize("name", ["deposit", "transfer", "reversal"])
    def test_carries_the_routing_fields(self, name):
        headers = dict(headers_for(fixture(name)))
        assert set(headers) == {"event_id", "event_type", "schema_version"}

    def test_values_match_the_payload(self):
        event = fixture("reversal")
        headers = dict(headers_for(event))

        assert headers["event_id"].decode() == event["event_id"]
        assert headers["event_type"].decode() == "transaction.reversal"
        assert headers["schema_version"].decode() == "1"

    def test_values_are_bytes(self):
        assert all(isinstance(v, bytes) for _, v in headers_for(fixture("deposit")))
