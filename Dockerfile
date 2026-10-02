FROM python:3.11-slim-bookworm

# Patch OS-level CVEs, then install system deps required by native packages and healthchecks
RUN apt-get update && apt-get upgrade -y && apt-get install -y --no-install-recommends \
    gcc g++ libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Ensure unbuffered logs for Cloud Run and prevent .pyc generation
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

# Copy production requirements first for efficient Docker layer caching
COPY requirements-prod.txt .
RUN pip install --no-cache-dir --prefer-binary -r requirements-prod.txt

# Copy application source code (evals, ui, and datasets remain excluded via .dockerignore)
COPY app/ ./app/

# Expose container port
EXPOSE 8080

# Healthcheck to verify FastAPI is responding
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${PORT}/ || exit 1

# Start production FastAPI server with dynamic Cloud Run PORT binding
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
