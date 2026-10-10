"""Execution trace records shown in the GUI (MCP tool calls and retrieval steps)."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ToolStatus = Literal["ok", "error", "invalid_input", "unavailable", "skipped"]


class ToolSpec(BaseModel):
    """A tool as discovered from an MCP server (tools/list)."""

    server: str
    name: str
    title: str | None
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    read_only: bool


class ToolCallRecord(BaseModel):
    """One MCP tool invocation, including the raw request and response for inspection."""

    kind: Literal["mcp_tool"] = "mcp_tool"
    step: int = 0  # set by the orchestrator in execution order
    server: str
    tool: str
    purpose: str = Field("", description="Why the orchestrator called this tool")
    arguments: dict[str, Any]
    status: ToolStatus
    error_code: str | None = None
    error_message: str | None = None
    result: dict[str, Any] | None = None
    api_calls: list[dict[str, Any]] = []
    retries: int = 0
    duration_ms: float
    trace_id: str
    started_at: datetime


class RetrievedRef(BaseModel):
    key: str
    label: str
    chunk_id: str
    dense_score: float
    keyword_score: float


class RetrievalRecord(BaseModel):
    """One RAG search, shown in the same execution timeline as the tool calls."""

    kind: Literal["rag_retrieval"] = "rag_retrieval"
    step: int = 0
    purpose: str
    query: str
    filters: dict[str, Any]
    status: Literal["ok", "error"]
    confidence: str | None = None
    top_dense_score: float | None = None
    results: list[RetrievedRef] = []
    quarantined_withheld: int = 0
    error_message: str | None = None
    duration_ms: float
    trace_id: str
    started_at: datetime
