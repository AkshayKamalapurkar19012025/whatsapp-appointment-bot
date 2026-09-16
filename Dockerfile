# Production image for the FastAPI app only -- the frontend is built and
# served separately by the nginx service (docker/nginx/Dockerfile), not
# bundled in here. Matches SETUP.md's "Python 3.11+" prerequisite exactly,
# rather than drifting to whatever the latest slim tag happens to be.
FROM python:3.11-slim

WORKDIR /app

# Prevents .pyc write-back into a read-only-ish layer and forces stdout/
# stderr to be unbuffered so `docker logs` shows output as it happens
# (matches uvicorn's own expectation of unbuffered logging).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Dependencies first, in their own layer, so a code-only change doesn't
# bust the pip-install cache on every rebuild.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Only what the running app and its own scripts (migrate.py, run from
# this same image at deploy time -- see SETUP.md/docker-compose.yml)
# need. No tests/, docs/, frontend/, or dev tooling.
COPY app/ ./app/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/

# Runs as a non-root user; MEDIA_ROOT (app/config.py) is created here and
# owned by that user since app/main.py mkdir's it again at startup and
# Pillow needs to write into it for uploaded doctor photos. The actual
# persistent volume is mounted over this path by docker-compose.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/media \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Plain urllib against /health (app/main.py) instead of curl/wget -- kept
# out of requirements.txt and the base image to keep the image small;
# Python's own standard library is already here.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)" || exit 1

# No --reload (that's the dev-only `uvicorn app.main:app --reload`
# workflow in SETUP.md) and no default worker count override -- a single
# uvicorn process per container, scaled by running more app containers if
# this VM ever needs it, not by adding in-process worker complexity this
# phase doesn't call for.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
