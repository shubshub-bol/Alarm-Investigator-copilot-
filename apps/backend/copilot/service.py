"""One copilot request end to end: understand -> MCP session (discover, playbook) -> grounded answer."""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from copilot.answer import CopilotAnswer, compose_answer
from copilot.conversations import ConversationStore, Turn
from copilot.investigation import Evidence, investigate
from copilot.llm import LlmProvider
from copilot.mcp_client import Connector, McpToolClient
from copilot.trace import RetrievalRecord, ToolCallRecord, ToolSpec
from copilot.understanding import QueryUnderstanding, understand
from rag.retrieval import Retriever

log = logging.getLogger("copilot.service")

TraceStep = Annotated[ToolCallRecord | RetrievalRecord, Field(discriminator="kind")]
EventCallback = Callable[[str, dict[str, Any]], Awaitable[None]]  # (event name, JSON-ready data)


class DiscoveryRecord(BaseModel):
    kind: Literal["mcp_discovery"] = "mcp_discovery"
    server: str
    connected: bool
    tool_count: int
    tools: list[str]
    error: str | None = None
    duration_ms: float


class Timings(BaseModel):
    understanding_ms: float
    investigation_ms: float
    answer_ms: float
    total_ms: float


class ChatResponse(BaseModel):
    conversation_id: str
    trace_id: str
    question: str
    status: Literal["ok", "partial", "degraded"]
    understanding: QueryUnderstanding
    answer: CopilotAnswer
    evidence: Evidence
    discovery: DiscoveryRecord
    trace: list[TraceStep]
    timings: Timings


class ToolsResponse(BaseModel):
    server: str
    connected: bool
    error: str | None
    tools: list[ToolSpec]


def new_trace_id() -> str:
    return f"trace-{uuid.uuid4().hex[:16]}"


def _ms(since: float) -> float:
    return round((time.perf_counter() - since) * 1000, 1)


class CopilotService:
    def __init__(
        self,
        connector: Connector,
        retriever: Retriever | None,
        llm: LlmProvider | None,
        conversations: ConversationStore | None = None,
        server_name: str = "alarm-management",
    ):
        self.connector = connector
        self.retriever = retriever
        self.llm = llm
        self.conversations = conversations or ConversationStore()
        self.server_name = server_name

    async def ask(
        self, question: str, conversation_id: str | None = None, on_event: EventCallback | None = None
    ) -> ChatResponse:
        """Run one question. If on_event is given, progress is reported as it happens (for streaming)."""

        async def emit(event: str, data: dict[str, Any]) -> None:
            if on_event is not None:
                await on_event(event, data)

        async def on_step(record: ToolCallRecord | RetrievalRecord) -> None:
            await emit("step", record.model_dump(mode="json"))

        started = time.perf_counter()
        trace_id = new_trace_id()
        conv = self.conversations.get_or_create(conversation_id)
        await emit("started", {"trace_id": trace_id, "conversation_id": conv.conversation_id})

        t0 = time.perf_counter()
        u = await understand(question, self.llm)
        understanding_ms = _ms(t0)
        await emit("understanding", u.model_dump(mode="json"))

        t0 = time.perf_counter()
        async with McpToolClient(self.connector, server_name=self.server_name) as mcp:
            tools = await mcp.discover()
            discovery = DiscoveryRecord(
                server=self.server_name,
                connected=mcp.connected,
                tool_count=len(tools),
                tools=[t.name for t in tools],
                error=None if mcp.connected else mcp.last_error,
                duration_ms=_ms(t0),
            )
            await emit("discovery", discovery.model_dump(mode="json"))
            run = await investigate(u, mcp, self.retriever, conv.state, trace_id=trace_id, on_step=on_step)
        investigation_ms = _ms(t0)

        t0 = time.perf_counter()
        answer = await compose_answer(question, run, self.llm)
        answer_ms = _ms(t0)

        failed = any(s.status != "ok" for s in run.steps)
        if not discovery.connected or answer.grounding.degraded or u.fallback_reason:
            status = "degraded"
        elif failed:
            status = "partial"
        else:
            status = "ok"
        conv.turns.append(
            Turn(
                question=question,
                intent=u.intent,
                summary=answer.summary,
                trace_id=trace_id,
                status=status,
                at=datetime.now(UTC),
            )
        )
        response = ChatResponse(
            conversation_id=conv.conversation_id,
            trace_id=trace_id,
            question=question,
            status=status,
            understanding=u,
            answer=answer,
            evidence=run.evidence,
            discovery=discovery,
            trace=run.steps,
            timings=Timings(
                understanding_ms=understanding_ms,
                investigation_ms=investigation_ms,
                answer_ms=answer_ms,
                total_ms=_ms(started),
            ),
        )
        log.info(
            "chat",
            extra={
                "fields": {
                    "trace_id": trace_id,
                    "conversation_id": conv.conversation_id,
                    "intent": u.intent,
                    "understanding": u.method,
                    "answer_mode": answer.grounding.mode,
                    "status": status,
                    "tool_calls": sum(isinstance(s, ToolCallRecord) for s in run.steps),
                    "retrievals": sum(isinstance(s, RetrievalRecord) for s in run.steps),
                    "citations": len(answer.citations),
                    "total_ms": response.timings.total_ms,
                }
            },
        )
        return response

    async def list_tools(self) -> ToolsResponse:
        async with McpToolClient(self.connector, server_name=self.server_name) as mcp:
            tools = await mcp.discover()
            return ToolsResponse(
                server=self.server_name,
                connected=mcp.connected,
                error=None if mcp.connected else mcp.last_error,
                tools=tools,
            )
