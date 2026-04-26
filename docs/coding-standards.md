# gitopsapi — Coding Standards

## LLM / Embedding Backend Timeouts

All HTTP clients and Gateway API routes that proxy to an LLM or embedding backend
(Ollama, vLLM, or any future model server) must use a read/request timeout of **≥ 120s**.

### Why 120s

Embedding calls on agentsonly Ollama (CPU-bound, no GPU) take up to 34s for 8000-char
chunks. 120s gives comfortable headroom for typical RAG chunks without approaching LB
idle timeouts (~300s). Empirically measured 2026-04-16 (see
`agenticflows/operations/network-timeouts.md`).

### Python HTTP client

```python
# httpx (preferred in gitopsapi)
async with httpx.AsyncClient(timeout=120.0) as http:
    response = await http.post(ollama_url, json=payload)

# explicit per-operation timeouts (connect + read)
timeout = httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=10.0)
```

### Gateway API HTTPRoute

Any HTTPRoute that proxies to an LLM or embedding backend must include:

```yaml
spec:
  rules:
    - filters:
        - type: URLRewrite
      timeouts:
        request: 120s
        backendRequest: 120s
```

### Scope

This convention applies to all clusters where Ollama or a similar backend is deployed.
As of 2026-04-26, Ollama runs on **agentsonly** only — gitopsete has no Ollama instance
and therefore no Ollama HTTPRoute. The convention applies from day one if an Ollama
instance is added to any cluster.

Streaming endpoints (`/api/chat` with `stream: true`) may require higher timeouts
(≥ 600s) — evaluate separately and document per-route if added.
