from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.db.connection import get_connection

router = APIRouter(
    prefix="/doctors",
    tags=["Doctor Blocks"],
)


class DoctorBlockCreate(BaseModel):
    start_at: datetime
    end_at: datetime
    reason: str = Field(min_length=1, max_length=255)

    @field_validator("end_at")
    @classmethod
    def validate_time_range(cls, value: datetime, info):
        start_at = info.data.get("start_at")

        if start_at is not None and value <= start_at:
            raise ValueError("End time must be after start time")

        return value

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("Block reason cannot be empty")

        return value


@router.get("/{doctor_id}/blocks")
def get_doctor_blocks(doctor_id: int):
    with get_connection() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT id
                FROM doctors
                WHERE id = %s
                  AND active = TRUE
                """,
                (doctor_id,),
            )

            doctor = cur.fetchone()

            if doctor is None:
                raise HTTPException(
                    status_code=404,
                    detail="Doctor not found",
                )

            cur.execute(
                """
                SELECT
                    id,
                    start_at,
                    end_at,
                    reason,
                    active
                FROM doctor_blocks
                WHERE doctor_id = %s
                  AND active = TRUE
                ORDER BY start_at
                """,
                (doctor_id,),
            )

            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "start_at": row[1].isoformat(),
            "end_at": row[2].isoformat(),
            "reason": row[3],
            "active": row[4],
        }
        for row in rows
    ]


@router.post("/{doctor_id}/blocks")
def create_doctor_block(
    doctor_id: int,
    block: DoctorBlockCreate,
):
    with get_connection() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT id
                FROM doctors
                WHERE id = %s
                  AND active = TRUE
                """,
                (doctor_id,),
            )

            doctor = cur.fetchone()

            if doctor is None:
                raise HTTPException(
                    status_code=404,
                    detail="Doctor not found",
                )

            cur.execute(
                """
                INSERT INTO doctor_blocks (
                    doctor_id,
                    start_at,
                    end_at,
                    reason
                )
                VALUES (%s, %s, %s, %s)
                RETURNING
                    id,
                    start_at,
                    end_at,
                    reason,
                    active
                """,
                (
                    doctor_id,
                    block.start_at,
                    block.end_at,
                    block.reason,
                ),
            )

            row = cur.fetchone()

    return {
        "id": row[0],
        "doctor_id": doctor_id,
        "start_at": row[1].isoformat(),
        "end_at": row[2].isoformat(),
        "reason": row[3],
        "active": row[4],
    }


@router.put("/{doctor_id}/blocks/{block_id}")
def update_doctor_block(
    doctor_id: int,
    block_id: int,
    block: DoctorBlockCreate,
):
    with get_connection() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT id
                FROM doctors
                WHERE id = %s
                  AND active = TRUE
                """,
                (doctor_id,),
            )

            doctor = cur.fetchone()

            if doctor is None:
                raise HTTPException(
                    status_code=404,
                    detail="Doctor not found",
                )

            cur.execute(
                """
                SELECT id
                FROM doctor_blocks
                WHERE id = %s
                  AND doctor_id = %s
                  AND active = TRUE
                """,
                (block_id, doctor_id),
            )

            existing_block = cur.fetchone()

            if existing_block is None:
                raise HTTPException(
                    status_code=404,
                    detail="Block not found",
                )

            cur.execute(
                """
                UPDATE doctor_blocks
                SET start_at = %s,
                    end_at = %s,
                    reason = %s
                WHERE id = %s
                  AND doctor_id = %s
                  AND active = TRUE
                RETURNING
                    id,
                    start_at,
                    end_at,
                    reason,
                    active
                """,
                (
                    block.start_at,
                    block.end_at,
                    block.reason,
                    block_id,
                    doctor_id,
                ),
            )

            row = cur.fetchone()

    return {
        "id": row[0],
        "doctor_id": doctor_id,
        "start_at": row[1].isoformat(),
        "end_at": row[2].isoformat(),
        "reason": row[3],
        "active": row[4],
    }


@router.delete("/{doctor_id}/blocks/{block_id}")
def delete_doctor_block(
    doctor_id: int,
    block_id: int,
):
    with get_connection() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT id
                FROM doctors
                WHERE id = %s
                  AND active = TRUE
                """,
                (doctor_id,),
            )

            doctor = cur.fetchone()

            if doctor is None:
                raise HTTPException(
                    status_code=404,
                    detail="Doctor not found",
                )

            cur.execute(
                """
                UPDATE doctor_blocks
                SET active = FALSE
                WHERE id = %s
                  AND doctor_id = %s
                  AND active = TRUE
                RETURNING id
                """,
                (block_id, doctor_id),
            )

            row = cur.fetchone()

            if row is None:
                raise HTTPException(
                    status_code=404,
                    detail="Block not found",
                )

    return {
        "id": block_id,
        "doctor_id": doctor_id,
        "message": "Block removed",
    }