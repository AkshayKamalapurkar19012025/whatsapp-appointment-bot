"""
Structured logging with a per-request correlation ID.

Every log line emitted while handling a request carries the same
request_id: sourced from an incoming X-Request-ID header if the caller
sent one, otherwise a freshly generated UUID. The same ID is echoed back
in the response's X-Request-ID header, so a caller (or a developer
correlating a bug report to server logs) can search logs for exactly
that request. Log lines emitted outside of a request (app startup,
scripts) show "-" instead of a stale/misleading ID.

Deliberately not adding a structured-logging dependency (no structlog,
no JSON logs) -- a request_id-tagged plain-text formatter is enough
value for the app's current size, per the instruction not to introduce
unnecessary complexity.
"""

import contextvars
import logging
import uuid

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"
        )
    )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def new_request_id() -> str:
    return uuid.uuid4().hex
