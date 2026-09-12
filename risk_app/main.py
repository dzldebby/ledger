from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from app.telemetry import configure_telemetry, instrument_app
from risk_app.database import close_pool, create_pool, get_conn
from risk_app.schemas import RiskDecisionRequest, RiskDecisionResponse
from risk_app.service import create_decision

configure_telemetry("risk-service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_pool()
    yield
    await close_pool()


app = FastAPI(title="Risk Decision Service", lifespan=lifespan)
instrument_app(app)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/risk-decisions", response_model=RiskDecisionResponse, status_code=201)
async def risk_decision(data: RiskDecisionRequest, conn=Depends(get_conn)):
    return await create_decision(conn, data)
