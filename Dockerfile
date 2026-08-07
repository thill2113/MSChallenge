# syntax=docker/dockerfile:1.7
#
# Builds the operations API image. This image cannot place trades: no live
# execution path exists in Phase 0/1 (ADR-005).

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /usr/local/bin/uv

WORKDIR /app


# --- dependencies -----------------------------------------------------------
FROM base AS deps

# Copied separately so a source-only change does not re-resolve dependencies.
COPY pyproject.toml README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/venv && VIRTUAL_ENV=/opt/venv uv pip install --no-cache .

ENV PATH="/opt/venv/bin:$PATH" VIRTUAL_ENV=/opt/venv


# --- runtime ----------------------------------------------------------------
FROM base AS runtime

COPY --from=deps /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    VIRTUAL_ENV=/opt/venv \
    PYTHONPATH=/app/src

COPY src/ ./src/
COPY docs/ ./docs/

# Non-root by default. Nothing in the running service writes to the image.
RUN useradd --create-home --uid 10001 trading \
    && chown -R trading:trading /app
USER trading

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz').read()"

CMD ["uvicorn", "observability.api:app", "--host", "0.0.0.0", "--port", "8000"]
