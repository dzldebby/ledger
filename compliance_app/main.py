import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from app.telemetry import configure_telemetry, instrument_app
from compliance_app.consumer import consume_forever
from compliance_app.database import close_pool, create_pool, get_conn
from compliance_app.schemas import ComplianceAlert, DeadLetterRecord
from compliance_app.service import list_alerts, list_dead_letters

configure_telemetry("compliance-service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_pool()
    consumer_task = asyncio.create_task(consume_forever())
    yield
    consumer_task.cancel()
    await asyncio.gather(consumer_task, return_exceptions=True)
    await close_pool()


app = FastAPI(title="Compliance Monitoring Service", lifespan=lifespan)
instrument_app(app)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/compliance-alerts", response_model=list[ComplianceAlert])
async def compliance_alerts(limit: int = 100, conn=Depends(get_conn)):
    return await list_alerts(conn, max(1, min(limit, 1000)))


@app.get("/dead-letter-events", response_model=list[DeadLetterRecord])
async def dead_letter_events(limit: int = 100, conn=Depends(get_conn)):
    return await list_dead_letters(conn, max(1, min(limit, 1000)))
