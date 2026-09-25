"""
OPD/HIMS master spec section 61 ("API ERROR FORMAT"): every error
response gets wrapped in the spec's {success, errorCode, message,
details} envelope, via three global exception handlers registered
once (register_exception_handlers) rather than touching each of the
100+ individual `raise HTTPException(...)` call sites across app/api/.

The spec's own wording is conditional -- "Standardize errors if the
existing application does not already have a suitable convention" --
and this app already has one (FastAPI's default {"detail": "..."}),
used consistently everywhere. So `detail` stays in every response body
here too, unchanged: every existing frontend caller (frontend/src/
api.ts's two request() helpers, both read response.json().detail)
keeps working exactly as it does today. This only adds the spec's
envelope fields alongside it.

errorCode is derived from the HTTP status code (NOT_FOUND, CONFLICT,
VALIDATION_ERROR, ...), not a real per-endpoint taxonomy like the
spec's own PATIENT_DUPLICATE example -- building one of those means
threading an explicit code through every individual raise site across
every app/api/*.py module, a much larger, separate undertaking. A
generic status-derived code is still a real, stable, machine-readable
value a caller can branch on (unlike parsing the free-text message),
just coarser-grained than a bespoke one.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

error_logger = logging.getLogger("app.error")

_ERROR_CODES_BY_STATUS = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "TOO_MANY_REQUESTS",
}


def _error_code_for_status(status_code: int) -> str:
    if status_code in _ERROR_CODES_BY_STATUS:
        return _ERROR_CODES_BY_STATUS[status_code]
    if 500 <= status_code < 600:
        return "INTERNAL_ERROR"
    return "ERROR"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException):
        detail = exc.detail
        message = detail if isinstance(detail, str) else "Request failed"
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "errorCode": _error_code_for_status(exc.status_code),
                "message": message,
                "details": {},
                "detail": detail,
            },
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        errors = jsonable_encoder(exc.errors())
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "errorCode": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": {"errors": errors},
                "detail": errors,
            },
        )

    # Master spec section 61: "Never expose stack traces to end users."
    # Every route in this app already turns expected failures into
    # HTTPException (caught above); anything that reaches here is a real
    # bug, logged in full server-side (request_id_middleware's own
    # request_id is already bound to this logger via logging_config, so
    # it lines up with the matching access-log line) but shown to the
    # caller only as a generic message, never exc's own text/traceback.
    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception):
        error_logger.exception("Unhandled exception for %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "errorCode": "INTERNAL_ERROR",
                "message": "An unexpected error occurred",
                "details": {},
                "detail": "Internal Server Error",
            },
        )
