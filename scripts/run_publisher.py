"""Runs the outbox publisher.

    python scripts/run_publisher.py

Reads DATABASE_URL, KAFKA_BOOTSTRAP_SERVERS and KAFKA_TOPIC from the
environment (or .env). Point DATABASE_URL at the deployed database and this
runs happily from your laptop - the publisher does not need to live next to
the API, it only needs to reach the database and the broker.

Stop with Ctrl-C. Stopping it is safe at any moment: unpublished rows stay
unpublished and are picked up on the next run.
"""
import asyncio
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
load_dotenv()

from app.services.publisher import run  # noqa: E402

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nstopped")
