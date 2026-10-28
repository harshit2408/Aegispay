FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations
RUN apt-get update \
    && apt-get install --yes --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install . \
    && useradd --create-home --uid 10001 aegis

USER aegis
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
