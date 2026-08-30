from fastapi import APIRouter, HTTPException

from app.db.connection import get_connection

router = APIRouter(
    prefix="/departments",
    tags=["Department Doctors"],
)


@router.get("/{department_id}/doctors")
def get_department_doctors(department_id: int):
    with get_connection() as conn:
        with conn.cursor() as cur:

            # Check department exists and is active
            cur.execute(
                """
                SELECT id
                FROM departments
                WHERE id = %s
                  AND active = TRUE
                """,
                (department_id,),
            )

            department = cur.fetchone()

            if department is None:
                raise HTTPException(
                    status_code=404,
                    detail="Department not found",
                )

            # Get active doctors assigned to department
            cur.execute(
                """
                SELECT
                    d.id,
                    d.name,
                    d.active
                FROM doctor_departments dd
                JOIN doctors d
                    ON d.id = dd.doctor_id
                WHERE dd.department_id = %s
                  AND d.active = TRUE
                ORDER BY d.name
                """,
                (department_id,),
            )

            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "name": row[1],
            "active": row[2],
        }
        for row in rows
    ]