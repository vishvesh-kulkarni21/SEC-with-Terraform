# Cloud Run image. The EDGAR cache and embedding indexes are baked in as a frozen data
# snapshot: fast cold starts and the same data the evaluation ran on. Tickers outside
# the snapshot are fetched at runtime into the (in-memory) container filesystem.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CACHE_DIR=/app/.cache PORT=8080

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install .

COPY .cache ./.cache
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn equity_research.api:app --host 0.0.0.0 --port ${PORT}"]
