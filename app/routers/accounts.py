from fastapi import APIRouter, Depends, HTTPException
from app.database import get_conn
from app.auth import get_authenticated_client
from app.schemas.accounts import AccountCreate, AccountHistoryResponse, AccountResponse, BalanceResponse
from app.services.accounts import create_account, get_balance, get_history, list_accounts

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.post("", response_model=AccountResponse, status_code=201)
async def create_account_endpoint(
    data: AccountCreate,
    client_id: str = Depends(get_authenticated_client),
    conn=Depends(get_conn),
):
    return await create_account(conn, data)


@router.get("", response_model=list[AccountResponse])
async def list_accounts_endpoint(
    client_id: str = Depends(get_authenticated_client),
    conn=Depends(get_conn),
):
    return await list_accounts(conn)


@router.get("/{account_id}/balance", response_model=BalanceResponse)
async def get_balance_endpoint(
    account_id: str,
    client_id: str = Depends(get_authenticated_client),
    conn=Depends(get_conn),
):
    balance = await get_balance(conn, account_id)
    if balance is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return balance


@router.get("/{account_id}/transactions", response_model=AccountHistoryResponse)
async def get_account_history_endpoint(
    account_id: str,
    limit: int = 100,
    client_id: str = Depends(get_authenticated_client),
    conn=Depends(get_conn),
):
    history = await get_history(conn, account_id, max(1, min(limit, 1000)))
    if history is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return history
