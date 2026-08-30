"""Watches one transaction travel from the deployed API into local Kafka.

    python scripts/demo_live_flow.py

Run the publisher in another terminal first - this script deliberately does
not start it, because the two being separate processes is the point being
demonstrated.

    # terminal 1
    $env:DATABASE_URL = "<render external url>"
    $env:KAFKA_BOOTSTRAP_SERVERS = "localhost:29092"
    python scripts/run_publisher.py

    # terminal 2
    $env:LEDGER_API_KEY = "<your key>"
    python scripts/demo_live_flow.py

What it does:

  1. Subscribes to the tail of the Kafka topic, so only new messages count.
  2. Posts a transfer to the deployed API, which commits the transaction and
     its outbox row together in a Postgres running in Oregon.
  3. Waits for that exact event to arrive in Kafka on this laptop, and reports
     how long the trip took.

The wait is the interesting part. Nothing pushes the event: the API has no
Kafka client and does not know the publisher exists. The publisher polls, finds
a row, sends it, and marks it. The delay you see is mostly the poll interval.
"""
import asyncio
import json
import os
import sys
import time
import urllib.request
import uuid

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv()

from aiokafka import AIOKafkaConsumer  # noqa: E402

API = os.getenv("LEDGER_API_URL", "https://ledger-api-8i8i.onrender.com")
KEY = os.getenv("LEDGER_API_KEY")
BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:29092")
TOPIC = os.getenv("KAFKA_TOPIC", "ledger.events")
WAIT_SECONDS = 60


def call(method, path, body=None, idempotent=False):
    headers = {"X-API-Key": KEY, "Content-Type": "application/json"}
    if idempotent:
        headers["Idempotency-Key"] = str(uuid.uuid4())
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    # The free instance sleeps after 15 minutes idle; a cold start can take
    # up to a minute, which is a spin-up not a failure.
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


async def main():
    if not KEY:
        sys.exit("LEDGER_API_KEY is not set")

    consumer = AIOKafkaConsumer(
        TOPIC,
        bootstrap_servers=BOOTSTRAP,
        # Only messages produced from now on. Without this we would match a
        # historical event and prove nothing.
        auto_offset_reset="latest",
        group_id=None,
    )
    await consumer.start()
    await consumer.seek_to_end()
    print(f"watching {TOPIC} at {BOOTSTRAP} for new messages\n")

    try:
        print(f"posting a transfer to {API}")
        suffix = uuid.uuid4().hex[:6]
        cash = call("POST", "/accounts", {"owner_id": f"cash-{suffix}", "account_type": "cash"})
        alice = call("POST", "/accounts", {"owner_id": f"alice-{suffix}", "account_type": "customer"})
        bob = call("POST", "/accounts", {"owner_id": f"bob-{suffix}", "account_type": "customer"})

        call("POST", "/transactions/deposit", {
            "account_id": alice["account_id"],
            "cash_account_id": cash["account_id"],
            "amount_minor": 80000,
        }, idempotent=True)

        started = time.monotonic()
        transfer = call("POST", "/transactions/transfer", {
            "from_account_id": alice["account_id"],
            "to_account_id": bob["account_id"],
            "amount_minor": 31337,
        }, idempotent=True)
        target = transfer["transaction_id"]
        print(f"  committed in Oregon: {target}")
        print(f"  waiting for it to reach Kafka on this laptop...\n")

        deadline = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < deadline:
            batches = await consumer.getmany(timeout_ms=1000)
            for _, messages in batches.items():
                for message in messages:
                    event = json.loads(message.value)
                    if event["data"]["transaction_id"] != target:
                        continue
                    elapsed = time.monotonic() - started
                    print(f"  ARRIVED after {elapsed:.1f}s")
                    print(f"    partition   {message.partition}, offset {message.offset}")
                    print(f"    key         {message.key.decode()}")
                    print(f"    headers     {[(k, v.decode()) for k, v in message.headers]}")
                    print(f"    event_id    {event['event_id']}")
                    print(f"    occurred_at {event['occurred_at']}")
                    print(f"    amount      {event['data']['postings'][0]['amount']}")
                    return

        print(f"  nothing arrived in {WAIT_SECONDS}s - is the publisher running,")
        print("  and is its DATABASE_URL pointed at the Render database?")
    finally:
        await consumer.stop()


if __name__ == "__main__":
    asyncio.run(main())
