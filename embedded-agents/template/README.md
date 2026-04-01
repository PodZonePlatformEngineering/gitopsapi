# Embedded Agent Template

Canonical FastAPI skeleton for podzone embedded agents. All embedded agents
(telegram-notifier, observability-logs, etc.) are built from this base.

See [ADR-001](../../docs/) and `agenticflows/types/embedded/README.md` in
podzoneAgentTeam for architectural decisions and deployment model.

## Directory structure

```
embedded-agents/
└── template/               ← this skeleton (PROJ-020/T-003)
    ├── agent/
    │   ├── main.py         ← FastAPI app + endpoints
    │   ├── card.py         ← Agent Card builder (from env vars)
    │   ├── ollama_client.py ← Injectable Ollama client
    │   └── logging_config.py ← Structured JSON logging
    ├── tests/
    │   └── test_template.py ← Mock-based tests (no Ollama required)
    ├── pyproject.toml
    ├── Dockerfile
    ├── docker-compose.yml
    └── .env.example        ← copy to .env for local dev
```

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/task` | Receive task payload; return structured JSON response |
| `GET` | `/health` | Liveness check; always 200 if operational |
| `GET` | `/.well-known/agent.json` | Agent Card (A2A capability descriptor) |

## Local dev

```bash
cp .env.example .env
# Edit .env: set AGENT_ID, AGENT_DOMAIN, AGENT_DESCRIPTION, AGENT_CAPABILITIES, OLLAMA_MODEL
# Ensure Ollama is running locally (default: http://localhost:11434)
docker compose up --build
```

Test the endpoints:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/.well-known/agent.json
curl -X POST http://localhost:8000/task \
  -H "Content-Type: application/json" \
  -d '{"task_type": "generate", "trigger_mode": "http", "payload": {"prompt": "hello"}}'
```

## Running tests (no Ollama required)

```bash
pip install -e ".[dev]"
pytest tests/
```

## Extending for a concrete agent

1. Copy the entire `template/` directory: `cp -r template/ my-agent/`
2. In `agent/main.py`, search `EXTEND HERE` and add:
   - Agent-specific fields to `TaskRequest` and `TaskResponse`
   - Real inference / action logic to `process_task()`
3. Add agent-specific env vars to `.env.example` (never commit actual values)
4. Update `AGENT_CAPABILITIES` in `.env.example` to match what the agent can do
5. Bump `AGENT_VERSION` if you change capabilities after first deployment

## Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AGENT_ID` | Yes | — | Unique agent identifier |
| `AGENT_DOMAIN` | Yes | — | Domain (e.g. `notification`, `observability`) |
| `AGENT_DESCRIPTION` | Yes | — | Human-readable description |
| `AGENT_CAPABILITIES` | Yes | — | Comma-separated capability list |
| `AGENT_TRIGGER_MODES` | No | `http,scheduled` | Comma-separated trigger modes |
| `AGENT_VERSION` | No | `0.1.0` | Semver — bump on capability changes |
| `OLLAMA_MODEL` | Yes | — | Ollama model name (e.g. `llama3.2`) |
| `OLLAMA_BASE_URL` | No | `http://ollama:11434` | Ollama service URL |

All agent-specific secrets (tokens, API keys, DSNs) must be added as env vars
and injected via Kubernetes Secrets at deployment. Never hard-code them.

## Secrets protocol

- No secrets in code, Dockerfiles, Agent Cards, or committed config
- In cluster: injected as Kubernetes Secrets (sourced via secretctl)
- Locally: `.env` file (gitignored — never commit)

## Logging

Every task invocation is logged to stdout in JSON format:

```json
{"level": "INFO", "logger": "agent.main", "message": "task completed",
 "extra": {"agent_id": "my-agent", "task_type": "generate",
            "trigger_mode": "http", "duration_ms": 312, "status": "ok"}}
```

Captured by the cluster logging stack (OpenSearch via the observability namespace).
