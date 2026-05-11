# ── KDS-AI Flask Application ──────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# System deps (curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App source
COPY . .

# Create non-root user
RUN useradd -m appuser && chown -R appuser /app
USER appuser

# gunicorn with threaded workers for Flask-Sock WebSocket support
CMD ["gunicorn", \
     "--workers", "1", \
     "--threads", "100", \
     "--bind", "0.0.0.0:8000", \
     "--timeout", "120", \
     "--log-level", "info", \
     "app:app"]
