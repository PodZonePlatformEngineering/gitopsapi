"""
Standalone seed script — seeds Qdrant with project documentation and source code.

Uses a single 'gitopsgui' collection with a 'type' field to distinguish content:
  - type='spec'            gitopsgui requirements and schema documents
  - type='planning'        team tasklist, operating model, planning docs
  - type='agent'           agent AGENTS.md role definitions
  - type='tasks'           task tracking files
  - type='source'          gitopsapi Python source code

Does NOT go through the MCP protocol; calls the core storage logic directly.

Usage:
    python -m gitopsgui.mcp.seed_context [--qdrant-url URL] [--ollama-url URL] [--wipe]
"""

import argparse
import asyncio
import glob
import os
import sys

# ---------------------------------------------------------------------------
# CLI argument parsing (done before importing context_server so env vars are
# set before the module-level constants are evaluated)
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed Qdrant with project docs and source")
    parser.add_argument(
        "--qdrant-url",
        default=os.environ.get("QDRANT_URL", "http://localhost:6333"),
        help="Qdrant base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--ollama-url",
        default=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
        help="Ollama base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--wipe",
        action="store_true",
        help="Delete and recreate the Qdrant collection before seeding",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Seed targets — updated for current repo layout (2026-03-15)
# ---------------------------------------------------------------------------

_BASE = "/Users/martincolley/workspace"
_AGENT_TEAM = f"{_BASE}/podzoneAgentTeam"
_GITOPSAPI  = f"{_BASE}/gitopsapi"
_GITOPSDOCS = f"{_BASE}/gitopsdocs"


def _md(path: str, source: str, type_: str) -> dict:
    return {"path": path, "source": source, "type": type_}


def _py(path: str) -> dict:
    return {"path": path, "source": os.path.relpath(path, _BASE), "type": "source"}


SEED_FILES: list[dict] = [
    # --- gitopsgui specifications (gitopsdocs repo) ---
    _md(f"{_GITOPSDOCS}/requirements/gitopsgui-requirements.md",
        "gitopsdocs/requirements/gitopsgui-requirements.md", "spec"),
    _md(f"{_GITOPSDOCS}/requirements/gitopsgui-blockers.md",
        "gitopsdocs/requirements/gitopsgui-blockers.md",     "spec"),
    _md(f"{_GITOPSDOCS}/requirements/gitopsgui-newrequirements.md",
        "gitopsdocs/requirements/gitopsgui-newrequirements.md", "spec"),

    # --- schemas ---
    *[
        _md(p, os.path.relpath(p, _GITOPSDOCS), "spec")
        for p in sorted(glob.glob(f"{_GITOPSDOCS}/schemas/*.md"))
    ],

    # --- agent team: operating model and planning ---
    _md(f"{_AGENT_TEAM}/planning/team-tasklist.md",     "planning/team-tasklist.md",     "tasks"),
    _md(f"{_AGENT_TEAM}/planning/completed-tasks.md",   "planning/completed-tasks.md",   "tasks"),
    _md(f"{_AGENT_TEAM}/planning/OPERATING-MODEL.md",   "planning/OPERATING-MODEL.md",   "planning"),
    _md(f"{_AGENT_TEAM}/planning/ROLE-PLAYER-MAPPING.md", "planning/ROLE-PLAYER-MAPPING.md", "planning"),
    _md(f"{_AGENT_TEAM}/planning/INTER-AGENT-MESSAGING.md", "planning/INTER-AGENT-MESSAGING.md", "planning"),

    # --- agent team: legacy planning docs (still present) ---
    _md(f"{_AGENT_TEAM}/AgentWorkflow.md",              "AgentWorkflow.md",              "planning"),
    _md(f"{_AGENT_TEAM}/Planning.md",                   "Planning.md",                   "planning"),
    _md(f"{_AGENT_TEAM}/planning/agentDelegation.md",   "planning/agentDelegation.md",   "planning"),

    # --- agent AGENTS.md definitions (auto-discovered) ---
    *[
        _md(p, os.path.relpath(p, _AGENT_TEAM), "agent")
        for p in sorted(glob.glob(f"{_AGENT_TEAM}/agents/*/AGENTS.md"))
    ],

    # --- agent team: requirements docs ---
    *[
        _md(p, os.path.relpath(p, _AGENT_TEAM), "spec")
        for p in sorted(glob.glob(f"{_AGENT_TEAM}/planning/requirements/*.md"))
    ],

    # --- gitopsapi source code (auto-discovered, excludes __init__ and __pycache__) ---
    *[
        _py(p)
        for p in sorted(glob.glob(f"{_GITOPSAPI}/src/**/*.py", recursive=True))
        if "__init__" not in os.path.basename(p) and "__pycache__" not in p
    ],
]

CHUNK_SIZE = 600


def _read_file(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


# ---------------------------------------------------------------------------
# Chunking helper
# ---------------------------------------------------------------------------

def chunk_text(content: str, chunk_size: int) -> list[str]:
    paragraphs = content.split("\n\n")
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if current and len(current) + len(para) + 2 > chunk_size:
            chunks.append(current.strip())
            current = para
        else:
            current = (current + "\n\n" + para).strip() if current else para
    if current.strip():
        chunks.append(current.strip())
    return chunks


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(qdrant_url: str, ollama_url: str, wipe: bool) -> None:
    os.environ["QDRANT_URL"] = qdrant_url
    os.environ["OLLAMA_URL"] = ollama_url

    from gitopsgui.mcp.context_server import (
        QDRANT_COLLECTION,
        ensure_collection,
        get_qdrant,
        store_chunk,
    )

    if wipe:
        print(f"Wiping collection '{QDRANT_COLLECTION}'...", flush=True)
        client = get_qdrant()
        collections = await client.get_collections()
        names = [c.name for c in collections.collections]
        if QDRANT_COLLECTION in names:
            await client.delete_collection(QDRANT_COLLECTION)
            print("  Collection deleted.", flush=True)
        else:
            print("  Collection did not exist, skipping delete.", flush=True)

    await ensure_collection()
    print(f"Collection '{QDRANT_COLLECTION}' ready.\n", flush=True)

    total_chunks = 0
    skipped = 0

    for entry in SEED_FILES:
        path = entry["path"]
        source = entry["source"]
        chunk_type = entry["type"]

        if not os.path.exists(path):
            print(f"  SKIP (not found): {source}", file=sys.stderr)
            skipped += 1
            continue

        content = await asyncio.to_thread(_read_file, path)

        if not content.strip():
            print(f"  SKIP (empty): {source}", file=sys.stderr)
            skipped += 1
            continue

        chunks = chunk_text(content, CHUNK_SIZE)
        for chunk in chunks:
            await store_chunk(text=chunk, source=source, chunk_type=chunk_type, tags=[])
        total_chunks += len(chunks)
        print(f"  {len(chunks):3d} chunks  [{chunk_type}]  {source}")

    print(f"\nDone. {total_chunks} chunks stored, {skipped} files skipped.")
    print("\nQuery by type with filter_type= one of: spec, planning, tasks, agent, source")


def main() -> None:
    args = parse_args()
    asyncio.run(run(
        qdrant_url=args.qdrant_url,
        ollama_url=args.ollama_url,
        wipe=args.wipe,
    ))


if __name__ == "__main__":
    main()
