"""Shared fixtures: a real retrieval index (built once) and an in-memory MCP -> simulator stack."""

from pathlib import Path

import pytest

from copilot.mcp_client import McpToolClient, in_memory_connector
from copilot_test_helpers import simulator_transport
from mcp_test_helpers import make_server
from rag.config import RagSettings
from rag.embeddings import OnnxBgeEmbedder
from rag.index_store import load_index
from rag.ingestion.pipeline import ingest
from rag.retrieval import Retriever

DOCS = Path(__file__).resolve().parents[3] / "rag" / "documents"


@pytest.fixture(scope="session")
def retriever(tmp_path_factory) -> Retriever:
    settings = RagSettings(document_path=DOCS, index_dir=tmp_path_factory.mktemp("copilot-index"))
    embedder = OnnxBgeEmbedder(cache_dir=settings.model_cache_dir)
    ingest(settings, embedder)
    return Retriever(load_index(settings.index_dir), embedder)


@pytest.fixture
async def mcp():
    async with McpToolClient(in_memory_connector(make_server(simulator_transport()))) as client:
        await client.discover()
        yield client
