from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
import psycopg

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
def create_department(department: DepartmentCreate):
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