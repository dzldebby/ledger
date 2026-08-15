from fastapi import APIRouter, HTTPException
from app.database import pool
from app.schemas.accounts import AccountCreate, AccountResponse
from app.services.accounts import create_account

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.post("", response_model=AccountResponse, status_code=201)
async def create_account_endpoint(data: AccountCreate):
    async with pool.acquire() as conn:
        return await create_account(conn, data)
