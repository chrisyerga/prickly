FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/app \
    NEEDLE_TELEMETRY=0

FROM base AS build
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev
# Fetch the native engine and both .cact weight files into ~/.cache/cactus-needle,
# and prove they load and run on this image's libc before it ships.
RUN mkdir -p "$HOME" && /app/.venv/bin/python -c "from prickly.engine import Engine; print(f'models ok in {Engine().load_ms} ms')"

FROM base AS runtime
RUN useradd --create-home --home-dir /home/app --uid 10001 app
COPY --from=build --chown=app:app /app /app
COPY --from=build --chown=app:app /home/app/.cache/cactus-needle /home/app/.cache/cactus-needle
USER app
WORKDIR /app
ENV PATH=/app/.venv/bin:$PATH \
    HF_HUB_OFFLINE=1 \
    PORT=8000
EXPOSE 8000
CMD ["uvicorn", "prickly.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
