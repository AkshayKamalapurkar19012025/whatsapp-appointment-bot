from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.departments import router as departments_router
from app.api.doctors import router as doctors_router
from app.api.doctor_schedule import router as doctor_schedule_router
from app.api.doctor_blocks import router as doctor_blocks_router
from app.api.availability import router as availability_router
from app.api.patients import router as patients_router
from app.api.appointments import router as appointments_router
from app.api.department_doctors import router as department_doctors_router
from app.api.booking import router as booking_router
from app.api.doctor_appointment_types import (
    router as doctor_appointment_types_router,
)

app = FastAPI()


app.include_router(
    health_router,
    prefix="/api",
)

app.include_router(
    departments_router,
    prefix="/api",
)

app.include_router(
    doctors_router,
    prefix="/api",
)

app.include_router(
    doctor_schedule_router,
    prefix="/api",
)

app.include_router(
    doctor_blocks_router,
    prefix="/api",
)

app.include_router(
    booking_router,
    prefix="/api",
)

app.include_router(
    availability_router,
    prefix="/api",
)

app.include_router(
    patients_router,
    prefix="/api",
)

app.include_router(
    appointments_router,
    prefix="/api",
)

app.include_router(
    department_doctors_router,
    prefix="/api",
)

app.include_router(
    doctor_appointment_types_router,
    prefix="/api",
)


@app.get("/health")
def health_check():
    return {"status": "ok"}