from pydantic import BaseModel


class AccountCreate(BaseModel):
    owner_id: str
    account_type: str


class AccountResponse(BaseModel):
    account_id: str
    owner_id: str
    account_type: str
    status: str
