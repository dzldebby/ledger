"""Reference consumer for ledger.events - for the Compliance service.

Runnable as-is against a local broker:

    pip install aiokafka
    python contracts/example_consumer.py

This is Python because the ledger is, but nothing here is Python-specific.
Every Kafka client has these same four settings and the same trade-offs; if
you are writing Java or Go, read the comments and ignore the syntax.

The contract this implements is contracts/events/README.md. Read the
"Delivery semantics" section there before this file - the settings below are
consequences of it, not arbitrary choices.
"""
import asyncio
import json
import os

from aiokafka import AIOKafkaConsumer

BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092")
TOPIC = os.getenv("KAFKA_TOPIC", "ledger.events")


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------
# Delivery is at-least-once: the same event WILL arrive more than once. A set
# is used here to keep the example short, but a set dies with the process -
# so after a restart every redelivered event looks new and gets processed
# twice. In the real service this must be a database table:
#
#     CREATE TABLE processed_events (event_id UUID PRIMARY KEY,
#                                    processed_at TIMESTAMPTZ DEFAULT NOW());
#
# and the check becomes an INSERT ... ON CONFLICT DO NOTHING, which is atomic
# and survives restarts. If it inserted zero rows, you have seen this event.
seen_event_ids: set[str] = set()

# Transactions we have been told about. Used only to demonstrate the
# out-of-order case below; the real service would query its own store.
known_transactions: set[str] = set()


def handle_event(event: dict) -> None:
    """Your compliance logic goes here. Called at most once per event_id."""
    data = event["data"]
    transaction_id = data["transaction_id"]
    known_transactions.add(transaction_id)

    total = sum(p["amount"] for p in data["postings"] if p["side"] == "debit")
    print(f"  {event['event_type']:<22} {transaction_id[:8]}  amount {total}")

    # Ordering is not guaranteed, and a reversal can arrive BEFORE the
    # transaction it reverses. That is normal, not an error - do not raise,
    # do not send it to a dead-letter queue, do not alert. Park it and resolve
    # it when the original turns up, or reconcile later using reversal_of_id.
    original = data.get("reversal_of_id")
    if original and original not in known_transactions:
        print(f"    (reverses {original[:8]}, which we have not seen yet - "
              f"parking it, this is expected)")


async def main() -> None:
    consumer = AIOKafkaConsumer(
        TOPIC,
        bootstrap_servers=BOOTSTRAP_SERVERS,

        # THE most important setting. group_id is what makes Kafka remember
        # your position. Without it you re-read the entire topic from scratch
        # on every restart. With it, Kafka stores your offset per partition
        # and you resume where you stopped.
        #
        # It also defines the unit of scaling: run two instances with the SAME
        # group_id and Kafka splits the partitions between them. Two instances
        # with DIFFERENT group_ids each get a full copy of every event - which
        # is how a second team would add their own consumer later without
        # affecting you.
        group_id="compliance",

        # Where to start when this group has no committed offset yet, i.e. the
        # very first run. "earliest" reads the whole history, which is what a
        # compliance service wants - it should see every transaction ever, not
        # only those after it happened to boot. Use "latest" only if history
        # is genuinely irrelevant to you.
        auto_offset_reset="earliest",

        # Commit offsets manually. See the commit call below for why this
        # matters more than it looks.
        enable_auto_commit=False,
    )

    await consumer.start()
    print(f"consuming {TOPIC} from {BOOTSTRAP_SERVERS} as group 'compliance'\n")

    try:
        async for message in consumer:
            event = json.loads(message.value)

            # Envelope fields are also on the Kafka headers, so a router could
            # dispatch on event_type without parsing the body at all:
            #   dict(message.headers)["event_type"]
            event_id = event["event_id"]

            if event_id in seen_event_ids:
                print(f"  duplicate {event_id[:8]} - skipped")
            else:
                seen_event_ids.add(event_id)
                handle_event(event)

            # Commit AFTER processing, never before.
            #
            #   Process then commit: a crash in between means the offset was
            #   never advanced, so the event is redelivered and the dedup
            #   check above catches it. Nothing is lost.
            #
            #   Commit then process: a crash in between means the offset moved
            #   past an event you never handled. It will never be redelivered.
            #   A transaction silently misses compliance review, and no error
            #   is ever raised.
            #
            # This is the exact mirror of the producer side, where the ledger
            # sends to Kafka before marking the outbox row published. Both
            # sides choose duplicates over loss, which is why dedup is
            # mandatory rather than optional.
            await consumer.commit()
    finally:
        await consumer.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nstopped")
