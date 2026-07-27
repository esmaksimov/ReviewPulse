FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_VERSION=2.4.1 \
    POETRY_NO_INTERACTION=1 \
    POETRY_VIRTUALENVS_CREATE=false

RUN pip install --no-cache-dir "poetry==${POETRY_VERSION}"

WORKDIR /app

# Dependencies first, from the lock file, so a code-only change does not re-resolve them.
COPY pyproject.toml poetry.lock README.md ./
RUN poetry install --only main --no-root

COPY src ./src
RUN poetry install --only main

# Alembic runs at startup and reads these from the working directory.
COPY alembic.ini ./
COPY migrations ./migrations

# SQLite lives on a volume so state survives a redeploy.
RUN mkdir -p /app/data
VOLUME ["/app/data"]

CMD ["python", "-m", "reviewpulse"]
