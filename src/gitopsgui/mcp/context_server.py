"""
MCP server for Qdrant-based context management for the gitopsgui project.

Run as: python -m gitopsgui.mcp.context_server
"""

import asyncio
import os
import uuid
from typing import Any

import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    TextContent,
    Tool,
)
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
QDRANT_COLLECTION = os.environ.get("QDRANT_COLLECTION", "gitopsgui")
VECTOR_SIZE = 768

# ---------------------------------------------------------------------------
# Shared clients (initialised lazily so the module can be imported safely)
# ---------------------------------------------------------------------------

_qdrant: AsyncQdrantClient | None = None


def get_qdrant() -> AsyncQdrantClient:
    global _qdrant
    if _qdrant is None:
        _qdrant = AsyncQdrantClient(url=QDRANT_URL)
    return _qdrant


# ---------------------------------------------------------------------------
# Collection bootstrap
# ---------------------------------------------------------------------------

async def ensure_collection() -> None:
    client = get_qdrant()
    collections = await client.get_collections()
    names = [c.name for c in collections.collections]
    if QDRANT_COLLECTION not in names:
        await client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
        # Create a keyword index on the 'type' field for filtering
        await client.create_payload_index(
            collection_name=QDRANT_COLLECTION,
            field_name="type",
            field_schema=PayloadSchemaType.KEYWORD,
        )


# ---------------------------------------------------------------------------
# Embedding helper
# ---------------------------------------------------------------------------

async def embed(text: str) -> list[float]:
    async with httpx.AsyncClient(timeout=60.0) as http:
        response = await http.post(
            f"{OLLAMA_URL}/api/embed",
            json={"model": OLLAMA_EMBED_MODEL, "input": text},
        )
        response.raise_for_status()
        data = response.json()
        # Ollama /api/embed returns {"embeddings": [[...]]}
        embeddings = data.get("embeddings") or data.get("embedding")
        if isinstance(embeddings[0], list):
            return embeddings[0]
        return embeddings


# ---------------------------------------------------------------------------
# Core logic (also called directly by seed_context.py)
# ---------------------------------------------------------------------------

async def store_chunk(
    text: str,
    source: str,
    chunk_type: str,
    tags: list[str],
) -> dict[str, Any]:
    await ensure_collection()
    vector = await embed(text)
    point_id = str(uuid.uuid4())
    await get_qdrant().upsert(
        collection_name=QDRANT_COLLECTION,
        points=[
            PointStruct(
                id=point_id,
                vector=vector,
                payload={
                    "text": text,
                    "source": source,
                    "type": chunk_type,
                    "tags": tags,
                },
            )
        ],
    )
    return {"id": point_id, "status": "stored"}


async def find_context(
    query: str,
    limit: int = 5,
    filter_type: str | None = None,
) -> list[dict[str, Any]]:
    await ensure_collection()
    vector = await embed(query)
    query_filter = None
    if filter_type:
        query_filter = Filter(
            must=[FieldCondition(key="type", match=MatchValue(value=filter_type))]
        )
    response = await get_qdrant().query_points(
        collection_name=QDRANT_COLLECTION,
        query=vector,
        limit=limit,
        query_filter=query_filter,
        with_payload=True,
    )
    return [
        {
            "score": hit.score,
            "text": hit.payload.get("text", ""),
            "source": hit.payload.get("source", ""),
            "type": hit.payload.get("type", ""),
            "tags": hit.payload.get("tags", []),
        }
        for hit in response.points
    ]


async def seed_file(
    file_path: str,
    source: str,
    chunk_type: str,
    chunk_size: int = 500,
) -> dict[str, Any]:
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Split at paragraph boundaries (double newlines)
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

    count = 0
    for chunk in chunks:
        await store_chunk(text=chunk, source=source, chunk_type=chunk_type, tags=[])
        count += 1
    return {"chunks_stored": count}


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

server = Server("gitopsgui-context")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="context-store",
            description="Store a chunk of text with metadata into Qdrant",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to embed and store"},
                    "metadata": {
                        "type": "object",
                        "description": "Metadata for the chunk",
                        "properties": {
                            "source": {"type": "string"},
                            "type": {"type": "string"},
                            "tags": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["source", "type"],
                    },
                },
                "required": ["text", "metadata"],
            },
        ),
        Tool(
            name="context-find",
            description="Semantic search for relevant context in Qdrant",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "limit": {"type": "integer", "default": 5, "description": "Max results"},
                    "filter_type": {
                        "type": "string",
                        "description": "Optional: filter results by payload type field",
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="context-seed",
            description="Seed the Qdrant collection from a markdown file",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to markdown file"},
                    "source": {"type": "string", "description": "Source label"},
                    "type": {"type": "string", "description": "Type label for all chunks"},
                    "chunk_size": {
                        "type": "integer",
                        "default": 500,
                        "description": "Target chunk size in characters",
                    },
                },
                "required": ["file_path", "source", "type"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    import json

    if name == "context-store":
        text = arguments["text"]
        meta = arguments.get("metadata", {})
        result = await store_chunk(
            text=text,
            source=meta.get("source", ""),
            chunk_type=meta.get("type", ""),
            tags=meta.get("tags", []),
        )
        return [TextContent(type="text", text=json.dumps(result))]

    elif name == "context-find":
        result = await find_context(
            query=arguments["query"],
            limit=arguments.get("limit", 5),
            filter_type=arguments.get("filter_type"),
        )
        return [TextContent(type="text", text=json.dumps(result))]

    elif name == "context-seed":
        result = await seed_file(
            file_path=arguments["file_path"],
            source=arguments["source"],
            chunk_type=arguments["type"],
            chunk_size=arguments.get("chunk_size", 500),
        )
        return [TextContent(type="text", text=json.dumps(result))]

    else:
        raise ValueError(f"Unknown tool: {name}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
