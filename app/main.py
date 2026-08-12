from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.database import create_pool, close_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_pool()
    yield
    await close_pool()


app = FastAPI(title="Ledger API", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}
