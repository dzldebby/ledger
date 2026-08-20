from fastapi import APIRouter, Depends, HTTPException
from app.database import get_conn
from app.schemas.accounts import AccountCreate, AccountResponse, BalanceResponse
from app.services.accounts import create_account, list_accounts, get_balance

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.post("", response_model=AccountResponse, status_code=201)
async def create_account_endpoint(data: AccountCreate, conn=Depends(get_conn)):
    return await create_account(conn, data)


@router.get("", response_model=list[AccountResponse])
async def list_accounts_endpoint(conn=Depends(get_conn)):
    return await list_accounts(conn)


@router.get("/{account_id}/balance", response_model=BalanceResponse)
async def get_balance_endpoint(account_id: str, conn=Depends(get_conn)):
    balance = await get_balance(conn, account_id)
    if balance is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return balance
