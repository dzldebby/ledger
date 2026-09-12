from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import get_authenticated_client
from app.database import get_conn
from app.schemas.settlements import (
    SettlementBatchResponse,
    SettlementConfirmation,
    SettlementConfirmationResponse,
    SettlementFailure,
    SettlementPrepare,
    SettlementResponse,
)
from app.services.settlements import (
    SettlementNotFoundError,
    SettlementStateError,
    confirm_settlement,
    fail_settlement,
    list_settlements,
    prepare_settlements,
)
from app.services.transactions import AccountNotFoundError, InsufficientFundsError

router = APIRouter(prefix="/settlements", tags=["settlements"])


@router.post("/prepare", response_model=SettlementBatchResponse, status_code=201)
async def prepare(
    request: Request,
    data: SettlementPrepare,
    client_id: str = Depends(get_authenticated_client),
    conn=Depends(get_conn),
):
    try:
        return await prepare_settlements(conn, data, request.state.correlation_id)
    except AccountNotFoundError:
        raise HTTPException(status_code=404, detail="External bank account not found")


@router.post("/{settlement_id}/confirm", response_model=SettlementConfirmationResponse)
async def confirm(
    settlement_id: str,
    request: Request,
    data: SettlementConfirmation,
    client_id: str = Depends(get_authenticated_client),
    conn=Depends(get_conn),
):
    try:
        return await confirm_settlement(
            conn,
            settlement_id,
            data.external_reference,
            request.state.correlation_id,
        )
    except SettlementNotFoundError:
        raise HTTPException(status_code=404, detail="Settlement not found")
    except SettlementStateError:
        raise HTTPException(status_code=409, detail="Settlement is not pending")
    except InsufficientFundsError:
        raise HTTPException(status_code=409, detail="Held funds are no longer available")


@router.post("/{settlement_id}/fail", response_model=SettlementResponse)
async def fail(
    settlement_id: str,
    data: SettlementFailure,
    client_id: str = Depends(get_authenticated_client),
    conn=Depends(get_conn),
):
    try:
        return await fail_settlement(conn, settlement_id, data.reason)
    except SettlementNotFoundError:
        raise HTTPException(status_code=404, detail="Settlement not found")
    except SettlementStateError:
        raise HTTPException(status_code=409, detail="Settlement is not pending")


@router.get("", response_model=list[SettlementResponse])
async def list_all(
    limit: int = 100,
    client_id: str = Depends(get_authenticated_client),
    conn=Depends(get_conn),
):
    return await list_settlements(conn, max(1, min(limit, 1000)))
