.PHONY: dev build test lint frontend-dev frontend-build

# Local development — API with hot-reload, frontend with Vite dev server
dev:
	uvicorn gitopsgui.api.main:app --reload --host 0.0.0.0 --port 8000

# Build the multi-stage Docker image
build:
	docker build -t gitopsgui:local .

# Run tests
test:
	pytest

# Lint Python
lint:
	ruff check src/

# Run frontend Vite dev server (proxies /api to localhost:8000)
frontend-dev:
	cd src/gitopsgui/frontend && npm run dev

# Build frontend bundle
frontend-build:
	cd src/gitopsgui/frontend && npm ci && npm run build

# Full local stack via docker-compose
up:
	docker compose up --build

down:
	docker compose down
