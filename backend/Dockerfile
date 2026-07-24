# Single image, two entrypoints (api / voice_worker) selected by compose/Railway.
FROM python:3.13-slim

# uv for fast, reproducible installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_SYSTEM_PYTHON=1

WORKDIR /app

# Install deps first for layer caching.
COPY pyproject.toml ./
RUN uv pip install --system -r pyproject.toml

COPY . .

# Default command is overridden per-service in compose / Railway.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
