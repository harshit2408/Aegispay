FROM python:3.12-slim

# Git is needed only by the settlement worker (R21 anchor); the API image stays without it (R22).
ARG INSTALL_GIT=0
# The API's sandboxed PDF parser shells out to /usr/bin/tesseract for the OCR-diff detector (R14/R22).
# Without it every upload fails closed as ANALYSIS_UNAVAILABLE. Settlement never parses PDFs.
ARG INSTALL_TESSERACT=1
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations
# tini runs as PID 1 and reaps orphans, e.g. tesseract left behind by a killed parser (R22).
RUN packages="tini"; \
    if [ "$INSTALL_GIT" = "1" ]; then packages="$packages git ca-certificates"; fi; \
    if [ "$INSTALL_TESSERACT" = "1" ]; then packages="$packages tesseract-ocr"; fi; \
    if [ -n "$packages" ]; then \
        apt-get update \
        && apt-get install --yes --no-install-recommends $packages \
        && rm -rf /var/lib/apt/lists/*; \
    fi \
    && pip install . \
    && useradd --create-home --uid 10001 aegis

USER aegis
EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
