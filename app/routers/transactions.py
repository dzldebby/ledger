from fastapi import APIRouter, Depends, Header, HTTPException, Request
from app.database import get_conn
from app.auth import get_authenticated_client
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
from app.services.risk import RiskDeclinedError, RiskReviewError, RiskUnavailableError

router = APIRouter(prefix="/transactions", tags=["transactions"])


@router.post("/deposit", response_model=TransactionResponse, status_code=201)
async def create_deposit_endpoint(
    request: Request,
    data: DepositCreate,
    client_id: str = Depends(get_authenticated_client),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    conn=Depends(get_conn),
):
    try:
        return await create_deposit(
            conn, data, client_id, idempotency_key, request.state.correlation_id
        )
    except SameAccountError:
        raise HTTPException(status_code=400, detail="account_id and cash_account_id must be different")
    except IdempotencyKeyReuseError:
        raise HTTPException(status_code=409, detail="Idempotency-Key already used with a different request")
    except RiskDeclinedError as exc:
        raise HTTPException(status_code=403, detail={"message": "Risk declined", "reasons": exc.reasons})
    except RiskReviewError as exc:
        raise HTTPException(status_code=409, detail={"message": "Risk review required", "reasons": exc.reasons})
    except RiskUnavailableError:
        raise HTTPException(status_code=503, detail="Risk service unavailable")


@router.post("/{transaction_id}/reverse", response_model=TransactionResponse, status_code=201)
async def create_reversal_endpoint(
    request: Request,
    transaction_id: str,
    client_id: str = Depends(get_authenticated_client),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    conn=Depends(get_conn),
):
    data = ReversalCreate(transaction_id=transaction_id)
    try:
        return await create_reversal(
            conn, data, client_id, idempotency_key, request.state.correlation_id
        )
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
    request: Request,
    data: TransferCreate,
    client_id: str = Depends(get_authenticated_client),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    conn=Depends(get_conn),
):
    try:
        return await create_transfer(
            conn, data, client_id, idempotency_key, request.state.correlation_id
        )
    except SameAccountError:
        raise HTTPException(status_code=400, detail="Cannot transfer to the same account")
    except InsufficientFundsError:
        raise HTTPException(status_code=400, detail="Insufficient funds")
    except AccountNotFoundError:
        raise HTTPException(status_code=404, detail="Account not found")
    except IdempotencyKeyReuseError:
        raise HTTPException(status_code=409, detail="Idempotency-Key already used with a different request")
    except RiskDeclinedError as exc:
        raise HTTPException(status_code=403, detail={"message": "Risk declined", "reasons": exc.reasons})
    except RiskReviewError as exc:
        raise HTTPException(status_code=409, detail={"message": "Risk review required", "reasons": exc.reasons})
    except RiskUnavailableError:
        raise HTTPException(status_code=503, detail="Risk service unavailable")
