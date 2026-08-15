from fastapi import APIRouter, Depends
from app.database import get_conn
from app.schemas.accounts import AccountCreate, AccountResponse
from app.services.accounts import create_account

router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.post("", response_model=AccountResponse, status_code=201)
async def create_account_endpoint(data: AccountCreate, conn=Depends(get_conn)):
    return await create_account(conn, data)
