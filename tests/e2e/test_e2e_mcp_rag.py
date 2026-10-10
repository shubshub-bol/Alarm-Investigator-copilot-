"""End-to-end: real HTTP between three running services, then the acceptance scenario through the backend.

backend API (/api/chat)  --MCP over streamable HTTP + bearer-->  MCP server
MCP server               --REST + bearer + trace headers----->   Alarm API simulator
backend                  --hybrid retrieval------------------>   RAG index (real bge embeddings)
"""

import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from alarm_api_client import ClientSettings
from alarm_mcp.app import create_http_app
from alarm_mcp.config import McpSettings
from alarm_simulator.config import Settings as SimulatorSettings
from alarm_simulator.main import create_app as create_simulator
from copilot.api import create_app as create_backend
from copilot.config import CopilotSettings
from copilot.mcp_client import http_connector
from copilot.service import CopilotService
from rag.config import RagSettings
from rag.embeddings import OnnxBgeEmbedder
from rag.index_store import load_index
from rag.ingestion.pipeline import ingest
from rag.retrieval import Retriever

API_TOKEN, MCP_TOKEN = "e2e-api-token", "e2e-mcp-token"
DOCS = Path(__file__).resolve().parents[2] / "rag" / "documents"
ACCEPTANCE = (
    "Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days, identify likely "
    "contributing factors, retrieve the relevant operating procedure, and provide recommended actions with source "
    "evidence."
)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RunningServer:
    """Run an ASGI app with uvicorn in a background thread on a real port."""

    def __init__(self, app, port: int):
        self.port = port
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> "RunningServer":
        self.thread.start()
        deadline = time.time() + 15
        while not self.server.started:
            if time.time() > deadline:
                raise RuntimeError(f"server on port {self.port} did not start")
            time.sleep(0.05)
        return self

    def __exit__(self, *exc) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)


@pytest.fixture(scope="module")
def retriever(tmp_path_factory) -> Retriever:
    settings = RagSettings(document_path=DOCS, index_dir=tmp_path_factory.mktemp("e2e-index"))
    embedder = OnnxBgeEmbedder(cache_dir=settings.model_cache_dir)
    ingest(settings, embedder)
    return Retriever(load_index(settings.index_dir), embedder)


@pytest.fixture(scope="module")
def mcp_url():
    api_port, mcp_port = free_port(), free_port()
    simulator = create_simulator(SimulatorSettings(api_token=API_TOKEN))
    mcp_app = create_http_app(
        McpSettings(auth_token=MCP_TOKEN, allowed_hosts=["127.0.0.1:*", "localhost:*"]),
        api_settings=ClientSettings(base_url=f"http://127.0.0.1:{api_port}", token=API_TOKEN),
    )
    with RunningServer(simulator, api_port), RunningServer(mcp_app, mcp_port):
        yield f"http://127.0.0.1:{mcp_port}/mcp"


def backend(mcp_url: str, token: str, retriever: Retriever) -> TestClient:
    service = CopilotService(connector=http_connector(mcp_url, token), retriever=retriever, llm=None)
    return TestClient(create_backend(CopilotSettings(_env_file=None), service=service))


def test_acceptance_scenario_end_to_end(mcp_url, retriever):
    with backend(mcp_url, MCP_TOKEN, retriever) as api:
        body = api.post("/api/chat", json={"message": ACCEPTANCE}).json()

    # 1. asset resolution through an MCP tool
    tool_steps = [s for s in body["trace"] if s["kind"] == "mcp_tool"]
    assert tool_steps[0]["tool"] == "search_assets"
    assert body["evidence"]["asset"]["asset_id"] == "AST-BFP-101"
    # 2. multi-step Alarm Management API chaining through MCP, over real HTTP, all with one trace id
    assert body["discovery"] == {**body["discovery"], "connected": True, "tool_count": 13}
    assert len(tool_steps) >= 8 and all(s["status"] == "ok" for s in tool_steps)
    assert all(s["result"]["meta"]["trace_id"] == body["trace_id"] for s in tool_steps)
    api_paths = {c["path"] for s in tool_steps for c in s["api_calls"]}
    assert {
        "/assets/search",
        "/alarms/summary",
        "/alarms/correlation",
        "/recommendations/operator-actions",
    } <= api_paths
    # 3. document retrieval through RAG
    retrievals = [s for s in body["trace"] if s["kind"] == "rag_retrieval"]
    assert retrievals and all(s["status"] == "ok" for s in retrievals)
    # 4. combined reasoning: contributing factor from correlation + document guidance + conflict check
    assert body["evidence"]["contributing_factors"][0]["leading"]["asset_id"] == "AST-MTR-101"
    assert any(a["verdict"] == "conflict" for a in body["answer"]["action_assessments"])
    # 5. citations
    labels = {c["label"] for c in body["answer"]["citations"]}
    assert {"SOP-BFP-001 §4.1", "SAF-005 §2"} <= labels
    for section in ("findings", "likely_causes", "procedure", "recommended_actions"):
        assert all(s["citations"] for s in body["answer"][section])
    # 6. GUI-ready output and 7. execution trace
    assert body["answer"]["markdown"].startswith("Boiler Feed Pump 101")
    assert [s["step"] for s in body["trace"]] == list(range(1, len(body["trace"]) + 1))
    assert body["status"] == "ok"


def test_wrong_mcp_token_is_refused_and_answer_degrades(mcp_url, retriever):
    with backend(mcp_url, "not-the-token", retriever) as api:
        body = api.post("/api/chat", json={"message": ACCEPTANCE}).json()
    assert body["discovery"]["connected"] is False
    assert body["status"] == "degraded"
    assert body["answer"]["citations"]  # still answers from documents, and says why
