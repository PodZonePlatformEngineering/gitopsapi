FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer caching)
COPY pyproject.toml .
RUN pip install --no-cache-dir ".[dev]" || pip install --no-cache-dir .

COPY agent/ ./agent/

# No secrets in the image — all injected at runtime via Kubernetes Secrets
ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
