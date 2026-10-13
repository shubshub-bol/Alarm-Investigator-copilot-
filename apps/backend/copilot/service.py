"""One copilot request end to end: small talk is answered at once; any other question opens an MCP session
(discover the tools), then the agent chooses the steps when a model is configured (agent.py) and a playbook chosen by
the keyword rules runs otherwise (also when the model fails before its first step, marked degraded) -> grounded
answer."""

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from copilot.agent import Agent, AgentRun, compose_agent_answer
from copilot.answer import CopilotAnswer, compose_answer, conversation_reply
from copilot.context import remember, render
from copilot.conversations import ConversationStore, OpenConversation, StorageError, new_conversation_id
from copilot.investigation import Evidence, investigate
from copilot.llm import LlmProvider
from copilot.mcp_client import Connector, McpToolClient
from copilot.titles import llm_title, rule_title
from copilot.trace import RetrievalRecord, ToolCallRecord, ToolSpec
from copilot.understanding import QueryUnderstanding, understand_with_rules
from rag.retrieval import Retriever

log = logging.getLogger("copilot.service")

TraceStep = Annotated[ToolCallRecord | RetrievalRecord, Field(discriminator="kind")]
EventCallback = Callable[[str, dict[str, Any]], Awaitable[None]]  # (event name, JSON-ready data)
TITLE_WAIT_SECONDS = 3.0  # how long a finished answer waits for a slow LLM title before using the rule title
NEEDS_MODEL = "This question needs the language model."


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
    discovery: DiscoveryRecord | None  # None when no MCP session was needed (small talk)
    trace: list[TraceStep]
    timings: Timings
    saved: bool = True  # False when storage failed: the chat has no address that can be opened again


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
        conv = await self._resume(conversation_id)
        await emit("started", {"trace_id": trace_id, "conversation_id": conv.conversation_id})

        t0 = time.perf_counter()
        chat = render(conv.state)  # the chat so far, for the model: recent turns in full, older ones in short
        u = understand_with_rules(question)  # small talk, and the reading used without a model
        understanding_ms = _ms(t0)
        await emit("understanding", u.model_dump(mode="json"))
        if u.intent == "conversation":
            response = await self._reply_without_tools(question, u, conv, trace_id, started, understanding_ms)
            remember(conv.state, question, response.answer.summary, response.evidence)
            await self._save(conv, question, response, title=None)  # small talk leaves the chat as "New chat"
            return response

        # The LLM title runs alongside the investigation and is only waited for once the answer is ready.
        title_task = asyncio.create_task(llm_title(self.llm, question)) if self.llm and conv.needs_title else None
        agent_run: AgentRun | None = None
        try:
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
                if self.llm is not None:
                    agent = Agent(question, chat, self.llm, mcp, tools, self.retriever, conv.state, trace_id, on_step)
                    agent_run = await agent.run()
                if agent_run is None or agent_run.failed_at_once:
                    run = await investigate(u, mcp, self.retriever, conv.state, trace_id=trace_id, on_step=on_step)
                else:
                    run = agent_run.run
            investigation_ms = _ms(t0)

            t0 = time.perf_counter()
            if agent_run is not None and not agent_run.failed_at_once:
                u = agent_run.understanding
                answer = compose_agent_answer(question, agent_run, self.llm)
            else:
                reason = agent_run.model_error if agent_run else None
                if reason:
                    u = u.model_copy(update={"fallback_reason": reason})
                uncovered = u.intent == "knowledge_question" and run.evidence.retrieval_confidence in ("low", "none")
                extra = [NEEDS_MODEL] if self.llm is None and uncovered else None
                answer = compose_answer(question, run, degraded_reason=reason, extra_gaps=extra)
            answer_ms = _ms(t0)
            title = await self._title(title_task, u, run.evidence, answer) if conv.needs_title else None
        finally:
            if title_task is not None:
                title_task.cancel()  # a no-op once it has finished; stops it if this question was cancelled

        agent_answered = agent_run is not None and not agent_run.failed_at_once
        # An input the schema check rejected is the agent's own slip, which the model saw and could correct: no gap in
        # the data. On the rules path it would be a bug, so there it counts.
        ok_statuses = ("ok", "invalid_input") if agent_answered else ("ok",)
        failed = any(s.status not in ok_statuses for s in run.steps)
        if not discovery.connected or answer.grounding.degraded or u.fallback_reason:
            status = "degraded"
        elif failed or answer.grounding.agent_hit_limit:
            status = "partial"
        else:
            status = "ok"
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
        said = " ".join([answer.summary, *(s.text for s in answer.analysis)])
        remember(conv.state, question, said, run.evidence)
        await self._save(conv, question, response, title)
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

    async def _reply_without_tools(
        self,
        question: str,
        u: QueryUnderstanding,
        conv: OpenConversation,
        trace_id: str,
        started: float,
        understanding_ms: float,
    ) -> ChatResponse:
        """Small talk and off-topic messages: a short reply, with no tools and no documents."""
        t0 = time.perf_counter()
        answer = await conversation_reply(question, self.llm)
        answer_ms = _ms(t0)
        log.info(
            "chat",
            extra={
                "fields": {
                    "trace_id": trace_id,
                    "intent": u.intent,
                    "answer_mode": answer.grounding.mode,
                    "tool_calls": 0,
                }
            },
        )
        return ChatResponse(
            conversation_id=conv.conversation_id,
            trace_id=trace_id,
            question=question,
            status="ok",
            understanding=u,
            answer=answer,
            evidence=Evidence(intent=u.intent),
            discovery=None,
            trace=[],
            timings=Timings(
                understanding_ms=understanding_ms, investigation_ms=0, answer_ms=answer_ms, total_ms=_ms(started)
            ),
        )

    async def _title(
        self,
        task: asyncio.Task[str | None] | None,
        u: QueryUnderstanding,
        evidence: Evidence,
        answer: CopilotAnswer,
    ) -> str:
        """The LLM title if it arrives in time and is usable, otherwise the rule-based title."""
        if task is not None:
            try:
                title = await asyncio.wait_for(task, TITLE_WAIT_SECONDS)
            except TimeoutError:
                log.warning("llm title too slow; using rules")
                title = None
            if title:
                return title
        return rule_title(u, evidence, [c.title for c in answer.citations])

    async def _resume(self, conversation_id: str | None) -> OpenConversation:
        """The conversation to continue. Without working storage: the same chat, without its earlier context."""
        try:
            return await asyncio.to_thread(self.conversations.resume, conversation_id)
        except StorageError as exc:
            log.error("could not load the conversation", extra={"fields": {"error": str(exc)}})
            return OpenConversation(conversation_id=conversation_id or new_conversation_id())

    async def _save(self, conv: OpenConversation, question: str, response: ChatResponse, title: str | None) -> None:
        """Save the answered turn. The answer still goes out when it is not saved (storage failed, or the chat was
        deleted meanwhile), marked `saved: false`; a storage failure is also logged and reported by health."""
        try:
            response.saved = await asyncio.to_thread(
                self.conversations.save_turn, conv, question, response.model_dump(mode="json"), title
            )
        except StorageError as exc:
            response.saved = False
            log.error(
                "could not save the turn",
                extra={
                    "fields": {
                        "trace_id": response.trace_id,
                        "conversation_id": conv.conversation_id,
                        "error": str(exc),
                    }
                },
            )

    async def list_tools(self) -> ToolsResponse:
        async with McpToolClient(self.connector, server_name=self.server_name) as mcp:
            tools = await mcp.discover()
            return ToolsResponse(
                server=self.server_name,
                connected=mcp.connected,
                error=None if mcp.connected else mcp.last_error,
                tools=tools,
            )
