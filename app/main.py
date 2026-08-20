from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from app.database import create_pool, close_pool
from app.routers import accounts, transactions


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_pool()
    yield
    await close_pool()


app = FastAPI(title="Ledger API", lifespan=lifespan)
app.include_router(accounts.router)
app.include_router(transactions.router)
app.mount("/ui", StaticFiles(directory="app/static", html=True), name="ui")


@app.get("/health")
async def health():
    return {"status": "ok"}
