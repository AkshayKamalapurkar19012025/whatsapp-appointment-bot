from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.db.connection import get_connection

router = APIRouter(
    prefix="/patients",
    tags=["Patients"],
)


def insert_patient(cur, name: str, whatsapp_number: str):
    cur.execute(
        """
        INSERT INTO patients (
            name,
            whatsapp_number
        )
        VALUES (%s, %s)
        ON CONFLICT (whatsapp_number) DO NOTHING
        RETURNING id, name, whatsapp_number
        """,
        (
            name,
            whatsapp_number,
        ),
    )

    row = cur.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "name": row[1],
        "whatsapp_number": row[2],
    }


class PatientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    whatsapp_number: str = Field(min_length=1, max_length=30)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("Patient name cannot be empty")

        return value

    @field_validator("whatsapp_number")
    @classmethod
    def validate_whatsapp_number(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("WhatsApp number cannot be empty")

        return value


@router.get("")
def get_patients():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, whatsapp_number
                FROM patients
                ORDER BY name
                """
            )

            rows = cur.fetchall()

    return [
        {
            "id": row[0],
            "name": row[1],
            "whatsapp_number": row[2],
        }
        for row in rows
    ]


@router.post("")
def create_patient(patient: PatientCreate):
    with get_connection() as conn:
        with conn.cursor() as cur:

            cur.execute(
                """
                SELECT id
                FROM patients
                WHERE whatsapp_number = %s
                """,
                (patient.whatsapp_number,),
            )

            if cur.fetchone() is not None:
                raise HTTPException(
                    status_code=409,
                    detail="Patient with this WhatsApp number already exists",
                )

            created_patient = insert_patient(
                cur,
                patient.name,
                patient.whatsapp_number,
            )

            if created_patient is None:
                raise HTTPException(
                    status_code=409,
                    detail="Patient with this WhatsApp number already exists",
                )

    return created_patient
