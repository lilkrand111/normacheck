ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_IMAGE} AS app-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TMPDIR=/tmp/normacheck \
    STORAGE_DIR=/data \
    FORWARDED_ALLOW_IPS=""
WORKDIR /app
COPY requirements.lock ./
RUN python -m pip install --no-cache-dir -r requirements.lock \
    && groupadd --gid 10001 normacheck \
    && useradd --uid 10001 --gid 10001 --no-create-home normacheck \
    && mkdir -p /data /tmp/normacheck \
    && chown 10001:10001 /data /tmp/normacheck
COPY app/ ./app/
COPY formal_check/ ./formal_check/
COPY formal_fix/ ./formal_fix/
COPY frontend/ ./frontend/
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--timeout-graceful-shutdown", "210", "--no-access-log"]

FROM app-base AS test
USER root
COPY requirements-dev.txt ./
RUN python -m pip install --no-cache-dir -r requirements-dev.txt
COPY tests/ ./tests/
USER 10001:10001
CMD ["python", "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"]

FROM app-base AS runtime
