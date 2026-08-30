from fastapi import APIRouter

from app.db.connection import get_connection

router = APIRouter()


@router.get("/health/db")
def database_health():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database()")
            database = cur.fetchone()[0]

    return {
        "status": "ok",
        "database": database,
    }
