# The dispatch service image (and the MLflow UI, which runs the same image with a different command).
# Build context is the dispatch/ project directory:  docker compose -f ops/docker-compose.yml build
# Not built or run on the machine this was written on (there is no Docker there); treat the first build as untested.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# lightgbm needs the OpenMP runtime
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY dispatch ./dispatch
COPY ops ./ops

# not root; /data is the DISPATCH_HOME volume
RUN useradd --create-home --uid 10001 dispatch && mkdir -p /data && chown dispatch /data
USER dispatch

ENV DISPATCH_HOME=/data \
    DISPATCH_ASSETS_FILE=/app/ops/assets.example.json \
    DISPATCH_MODEL_ALIAS=paper

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=4)" || exit 1

# One process: the audit log and the paper ledger have a single writer per DISPATCH_HOME (see dispatch/execution/audit.py).
CMD ["python", "-m", "uvicorn", "dispatch.service.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
