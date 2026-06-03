FROM python:3.12-slim

LABEL maintainer="Scorebot Team"
LABEL description="Scorebot-Core-Lite: Lightweight scoring engine using FastAPI"

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy scorebot_core_lite package
COPY . .

# Expose FastAPI port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/healthz', timeout=3).raise_for_status()" || exit 1

# Run with uvicorn
CMD ["uvicorn", "scorebot_core_lite.main:app", "--host", "0.0.0.0", "--port", "8000"]
