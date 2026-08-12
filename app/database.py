import os
import asyncpg
from dotenv import load_dotenv

load_dotenv()

pool: asyncpg.Pool | None = None


async def create_pool() -> None:
    global pool
    pool = await asyncpg.create_pool(os.getenv("DATABASE_URL"))


async def close_pool() -> None:
    if pool:
        await pool.close()


async def get_conn() -> asyncpg.Connection:
    return await pool.acquire()
