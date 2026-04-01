FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Non-root user for OCI container security policy
RUN adduser --disabled-password --gecos "" appuser && chown -R appuser /app
USER appuser

# PORT env var respected by OCI's container runtime
ENV PORT=8000

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
