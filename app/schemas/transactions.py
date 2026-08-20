from pydantic import BaseModel, Field


class DepositCreate(BaseModel):
    account_id: str
    cash_account_id: str
    amount_minor: int = Field(gt=0)


class TransferCreate(BaseModel):
    from_account_id: str
    to_account_id: str
    amount_minor: int = Field(gt=0)


class ReversalCreate(BaseModel):
    transaction_id: str


class PostingResponse(BaseModel):
    account_id: str
    side: str
    amount_minor: int


class TransactionResponse(BaseModel):
    transaction_id: str
    type: str
    state: str
    postings: list[PostingResponse]
    reversal_of_id: str | None = None
