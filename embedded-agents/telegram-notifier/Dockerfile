FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY agent/ ./agent/

# Config (channel registry) is mounted at /config/channel-registry.yaml
# Reporting sink is mounted at /reporting
# All secrets injected via Kubernetes Secrets

ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "agent.main:app", "--host", "0.0.0.0", "--port", "8000"]
