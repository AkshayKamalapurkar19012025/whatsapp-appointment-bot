import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import ALLOWED_ORIGINS, MEDIA_ROOT
from app.db.connection import open_pool, close_pool
from app.logging_config import configure_logging, new_request_id, request_id_var
from app.api.health import router as health_router
from app.api.app_config import router as app_config_router
from app.api.dashboard import router as dashboard_router
from app.api.patient_auth import router as patient_auth_router
from app.api.staff_auth import router as staff_auth_router
from app.api.patient_scheduling import router as patient_scheduling_router
from app.api.appointment_types import router as appointment_types_router
from app.api.departments import router as departments_router
from app.api.doctors import router as doctors_router
from app.api.doctor_photo import router as doctor_photo_router
from app.api.doctor_schedule import router as doctor_schedule_router
from app.api.doctor_blocks import router as doctor_blocks_router
from app.api.availability import router as availability_router
from app.api.patients import router as patients_router
from app.api.appointments import router as appointments_router
from app.api.department_doctors import router as department_doctors_router
from app.api.department_appointment_types import (
    router as department_appointment_types_router,
)
from app.api.scheduling import router as scheduling_router
from app.api.doctor_appointment_types import (
    router as doctor_appointment_types_router,
)
from app.api.audit_log import router as audit_log_router
from app.api.queue_display import router as queue_display_router
from app.api.clinical import router as clinical_router
from app.api.orders import router as orders_router
from app.api.pharmacy import prescription_router, pharmacy_router
from app.api.billing import router as billing_router
from app.api.exceptions import router as exceptions_router
from app.api.packages import router as packages_router

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

# WEB P10 (security pass): only lets a browser-based frontend on an
# explicitly allowlisted origin (app.config.ALLOWED_ORIGINS) call this
# API cross-origin. Every request here is Bearer-token authenticated,
# never cookie-based, so allow_credentials stays False -- there is no
# ambient browser credential (a cookie) this API could leak cross-site,
# only whatever Authorization header the calling page's own JavaScript
# already chose to attach. With ALLOWED_ORIGINS unset (the default), this
# denies every cross-origin browser request, which matches every
# deployment shape used so far: FastAPI serving the frontend same-origin,
# or frontend/vite.config.ts's dev-time proxy -- neither is ever
# "cross-origin" from the browser's point of view.
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


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
    app_config_router,
    prefix="/api",
)

app.include_router(
    dashboard_router,
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
    patient_scheduling_router,
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
    doctor_photo_router,
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
    scheduling_router,
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
    department_appointment_types_router,
    prefix="/api",
)

app.include_router(
    doctor_appointment_types_router,
    prefix="/api",
)

app.include_router(
    audit_log_router,
    prefix="/api",
)

app.include_router(
    queue_display_router,
    prefix="/api",
)

app.include_router(
    clinical_router,
    prefix="/api",
)

app.include_router(
    orders_router,
    prefix="/api",
)

app.include_router(
    prescription_router,
    prefix="/api",
)

app.include_router(
    pharmacy_router,
    prefix="/api",
)

app.include_router(
    billing_router,
    prefix="/api",
)

app.include_router(
    exceptions_router,
    prefix="/api",
)

app.include_router(
    packages_router,
    prefix="/api",
)


@app.get("/health")
def health_check():
    return {"status": "ok"}


# Serves uploaded doctor profile photos (app/api/doctor_photo.py) straight
# off local disk -- see app/config.py's MEDIA_ROOT docstring for why this
# is disk-backed rather than object storage. StaticFiles requires the
# directory to already exist at mount time, hence the mkdir; the /doctors
# subdirectory itself is created lazily by the first upload.
MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=MEDIA_ROOT), name="media")