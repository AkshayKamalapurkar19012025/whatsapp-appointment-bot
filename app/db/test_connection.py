from app.db.connection import get_connection


with get_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT current_database(), current_user")
        print(cur.fetchone())
