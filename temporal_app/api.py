import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from temporalio.client import Client
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from app.telemetry import configure_telemetry, instrument_app
from temporal_app.client import connect_temporal
from temporal_app.models import SettlementBatchInput
from temporal_app.workflows import SettlementBatchWorkflow

configure_telemetry("temporal-settlement-api")

TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "ledger-settlements")
TEMPORAL_UI_URL = os.getenv("TEMPORAL_UI_URL", "http://localhost:8081").rstrip("/")


class StartBatchRequest(BaseModel):
    batch_id: str | None = Field(default=None, min_length=1, max_length=128)
    payout_failures_before_success: int = Field(default=0, ge=0, le=4)
    simulate_bank_rejection: bool = False
    payout_delay_seconds: int = Field(default=0, ge=0, le=15)
    workflow_delay_seconds: int = Field(default=0, ge=0, le=30)


class StartBatchResponse(BaseModel):
    workflow_id: str
    started: bool
    temporal_ui_url: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.temporal = await connect_temporal()
    yield


app = FastAPI(title="Temporal Settlement API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Correlation-ID", "traceparent"],
    expose_headers=["X-Correlation-ID", "X-Trace-ID", "traceparent"],
)
instrument_app(app)


def _client(request: Request) -> Client:
    return request.app.state.temporal


@app.get("/health")
async def health(request: Request):
    return {"status": "ok", "temporal_connected": bool(_client(request))}


@app.post("/temporal/settlements/run", response_model=StartBatchResponse, status_code=202)
async def start_batch(request: Request, data: StartBatchRequest | None = None):
    batch_id = (
        data.batch_id
        if data and data.batch_id
        else datetime.now(timezone.utc).strftime("%Y-%m-%d")
    )
    workflow_id = f"settlement-batch-{batch_id}"
    started = True
    try:
        await _client(request).start_workflow(
            SettlementBatchWorkflow.run,
            SettlementBatchInput(
                batch_id=batch_id,
                correlation_id=request.state.correlation_id,
                payout_failures_before_success=(
                    data.payout_failures_before_success if data else 0
                ),
                simulate_bank_rejection=(
                    data.simulate_bank_rejection if data else False
                ),
                payout_delay_seconds=data.payout_delay_seconds if data else 0,
                workflow_delay_seconds=data.workflow_delay_seconds if data else 0,
            ),
            id=workflow_id,
            task_queue=TASK_QUEUE,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
        )
    except WorkflowAlreadyStartedError:
        started = False

    return StartBatchResponse(
        workflow_id=workflow_id,
        started=started,
        temporal_ui_url=f"{TEMPORAL_UI_URL}/namespaces/default/workflows/{workflow_id}",
    )


@app.get("/temporal/workflows/{workflow_id}")
async def workflow_status(request: Request, workflow_id: str):
    try:
        description = await _client(request).get_workflow_handle(workflow_id).describe()
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Workflow not found") from exc
    return {
        "workflow_id": workflow_id,
        "run_id": description.run_id,
        "status": description.status.name.lower(),
        "temporal_ui_url": f"{TEMPORAL_UI_URL}/namespaces/default/workflows/{workflow_id}",
    }
