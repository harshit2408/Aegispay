FROM python:3.12-slim

# Git is needed only by the settlement worker (R21 anchor); the API image stays without it (R22).
ARG INSTALL_GIT=0
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations
RUN if [ "$INSTALL_GIT" = "1" ]; then \
        apt-get update \
        && apt-get install --yes --no-install-recommends git ca-certificates \
        && rm -rf /var/lib/apt/lists/*; \
    fi \
    && pip install . \
    && useradd --create-home --uid 10001 aegis

USER aegis
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
