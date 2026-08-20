from fastapi import APIRouter, Depends, Header, HTTPException
from app.database import get_conn
from app.schemas.transactions import DepositCreate, TransferCreate, ReversalCreate, TransactionResponse
from app.services.transactions import (
    create_deposit,
    create_transfer,
    create_reversal,
    SameAccountError,
    AccountNotFoundError,
    InsufficientFundsError,
    IdempotencyKeyReuseError,
    TransactionNotFoundError,
    AlreadyReversedError,
    CannotReverseReversalError,
)

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.post("/deposit", response_model=TransactionResponse, status_code=201)
async def create_deposit_endpoint(
    data: DepositCreate,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    conn=Depends(get_conn),
):
    try:
        return await create_deposit(conn, data, idempotency_key)
    except SameAccountError:
        raise HTTPException(status_code=400, detail="account_id and cash_account_id must be different")
    except IdempotencyKeyReuseError:
        raise HTTPException(status_code=409, detail="Idempotency-Key already used with a different request")


@router.post("/{transaction_id}/reverse", response_model=TransactionResponse, status_code=201)
async def create_reversal_endpoint(
    transaction_id: str,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    conn=Depends(get_conn),
):
    data = ReversalCreate(transaction_id=transaction_id)
    try:
        return await create_reversal(conn, data, idempotency_key)
    except TransactionNotFoundError:
        raise HTTPException(status_code=404, detail="Transaction not found")
    except AlreadyReversedError:
        raise HTTPException(status_code=409, detail="Transaction has already been reversed")
    except CannotReverseReversalError:
        raise HTTPException(status_code=400, detail="Cannot reverse a reversal transaction")
    except InsufficientFundsError:
        raise HTTPException(status_code=400, detail="Insufficient funds to reverse this transaction")
    except IdempotencyKeyReuseError:
        raise HTTPException(status_code=409, detail="Idempotency-Key already used with a different request")


@router.post("/transfer", response_model=TransactionResponse, status_code=201)
async def create_transfer_endpoint(
    data: TransferCreate,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    conn=Depends(get_conn),
):
    try:
        return await create_transfer(conn, data, idempotency_key)
    except SameAccountError:
        raise HTTPException(status_code=400, detail="Cannot transfer to the same account")
    except InsufficientFundsError:
        raise HTTPException(status_code=400, detail="Insufficient funds")
    except AccountNotFoundError:
        raise HTTPException(status_code=404, detail="Account not found")
    except IdempotencyKeyReuseError:
        raise HTTPException(status_code=409, detail="Idempotency-Key already used with a different request")
