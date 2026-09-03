import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.db.connection import open_pool, close_pool
from app.logging_config import configure_logging, new_request_id, request_id_var
from app.api.health import router as health_router
from app.api.patient_auth import router as patient_auth_router
from app.api.staff_auth import router as staff_auth_router
from app.api.patient_booking import router as patient_booking_router
from app.api.appointment_types import router as appointment_types_router
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

configure_logging()
access_logger = logging.getLogger("app.access")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Deliberately does NOT run database migrations. Schema changes are an
    # explicit, separate operation (`python scripts/migrate.py`, see
    # SETUP.md) run before starting the app -- not a side effect of the
    # app process starting, which would let a bad migration take down
    # every instance in a rolling deploy at once.
    open_pool()
    yield
    close_pool()


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """
    Tag every log line emitted while handling this request with the same
    request_id (propagates into sync route handlers too -- FastAPI runs
    them in a threadpool with the context copied along, verified in this
    change's own testing). Echoes the ID back in the response header so a
    client-reported issue can be matched to server logs.
    """
    incoming_id = request.headers.get("x-request-id")
    request_id = incoming_id or new_request_id()
    token = request_id_var.set(request_id)

    started_at = time.monotonic()
    status_code = 500

    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        duration_ms = (time.monotonic() - started_at) * 1000
        access_logger.info(
            "%s %s -> %s (%.1fms)",
            request.method,
            request.url.path,
            status_code,
            duration_ms,
        )
        request_id_var.reset(token)


app.include_router(
    health_router,
    prefix="/api",
)

app.include_router(
    patient_auth_router,
    prefix="/api",
)

app.include_router(
    staff_auth_router,
    prefix="/api",
)

app.include_router(
    patient_booking_router,
    prefix="/api",
)

app.include_router(
    appointment_types_router,
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