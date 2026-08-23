from fastapi import APIRouter, Depends
from app.database import get_conn
from app.auth import get_authenticated_client
from app.schemas.outbox import OutboxStats
from app.services.outbox import get_stats

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/outbox/stats", response_model=OutboxStats)
async def outbox_stats_endpoint(
    client_id: str = Depends(get_authenticated_client),
    conn=Depends(get_conn),
):
    """Aggregate outbox counts, for checking the outbox in a deployment where
    the database cannot be reached directly."""
    return await get_stats(conn)
