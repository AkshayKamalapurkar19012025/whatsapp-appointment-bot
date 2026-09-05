from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
import psycopg

from app.api.staff_auth import require_role
from app.db.connection import get_connection

router = APIRouter(prefix="/departments", tags=["Departments"])


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("Department name cannot be empty")

        return value


@router.get("")
def get_departments():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, active
                FROM departments
                WHERE active = TRUE
                ORDER BY name
                """
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


@router.post("")
def create_department(
    department: DepartmentCreate,
    admin: dict = Depends(require_role("ADMIN")),
):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO departments (name)
                    VALUES (%s)
                    RETURNING id, name, active
                    """,
                    (department.name,),
                )
                row = cur.fetchone()

        return {
            "id": row[0],
            "name": row[1],
            "active": row[2],
        }

    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            status_code=409,
            detail="Department already exists",
        )


@router.put("/{department_id}")
def update_department(
    department_id: int,
    department: DepartmentCreate,
    admin: dict = Depends(require_role("ADMIN")),
):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE departments
                    SET name = %s,
                        updated_at = NOW()
                    WHERE id = %s
                      AND active = TRUE
                    RETURNING id, name, active
                    """,
                    (department.name, department_id),
                )
                row = cur.fetchone()

                if row is None:
                    raise HTTPException(status_code=404, detail="Department not found")

        return {
            "id": row[0],
            "name": row[1],
            "active": row[2],
        }

    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            status_code=409,
            detail="Department already exists",
        )


@router.delete("/{department_id}")
def delete_department(
    department_id: int,
    admin: dict = Depends(require_role("ADMIN")),
):
    # Soft delete only, same as doctors/doctor_schedule -- departments are
    # referenced by doctor_departments and appointments.department_id, so
    # a hard DELETE would either fail on the FK or silently orphan rows.
    # Every listing endpoint already filters on active = TRUE.
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE departments
                SET active = FALSE,
                    updated_at = NOW()
                WHERE id = %s
                  AND active = TRUE
                RETURNING id
                """,
                (department_id,),
            )

            if cur.fetchone() is None:
                raise HTTPException(status_code=404, detail="Department not found")

    return {
        "id": department_id,
        "message": "Department removed",
    }