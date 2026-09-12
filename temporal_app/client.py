import asyncio
import os

from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor

TEMPORAL_ADDRESS = os.getenv("TEMPORAL_ADDRESS", "temporal:7233")
TEMPORAL_NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "default")


async def connect_temporal(attempts: int = 30) -> Client:
    last_error = None
    for attempt in range(attempts):
        try:
            return await Client.connect(
                TEMPORAL_ADDRESS,
                namespace=TEMPORAL_NAMESPACE,
                interceptors=[TracingInterceptor()],
            )
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                await asyncio.sleep(2)
    raise RuntimeError(f"could not connect to Temporal at {TEMPORAL_ADDRESS}") from last_error
