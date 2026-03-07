# Stage 1 — build React frontend
FROM node:20-alpine AS frontend-build
WORKDIR /app/frontend
COPY src/gitopsgui/frontend/package.json src/gitopsgui/frontend/package-lock.json* ./
RUN npm ci
COPY src/gitopsgui/frontend/ .
RUN npm run build

# Stage 2 — Python API
FROM python:3.11-slim AS api
WORKDIR /app

# Install Python dependencies
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

# Copy Python source
COPY src/ ./src/

# Copy compiled frontend into the location FastAPI serves static files from
COPY --from=frontend-build /app/frontend/dist ./src/gitopsgui/frontend/dist

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "gitopsgui.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
