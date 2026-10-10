"""HTTP API for the GUI.

POST /api/chat                      ask a question (optionally continuing a conversation)
GET  /api/tools                     MCP tool discovery view (names, descriptions, schemas)
GET  /api/health                    MCP / RAG / LLM readiness
GET  /api/conversations/{id}        conversation history
GET  /api/examples                  example questions for the GUI
"""

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from copilot import __version__
from copilot.config import CopilotSettings
from copilot.conversations import Conversation, ConversationStore
from copilot.llm import LlmProvider, build_provider
from copilot.mcp_client import http_connector
from copilot.service import ChatResponse, CopilotService, ToolsResponse
from rag.config import RagSettings
from rag.retrieval import Retriever

log = logging.getLogger("copilot.api")

EXAMPLES = [
    "Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days, identify likely "
    "contributing factors, retrieve the relevant operating procedure, and provide recommended actions with source "
    "evidence.",
    "Show active critical alarms for Boiler Feed Pump 102 and recommend immediate actions.",
    "Why are compressor discharge pressure alarms repeatedly occurring?",
    "Which alarm has the highest priority in EastRefinery, and why?",
    "What related assets should be inspected for this motor trip alarm?",
    "Which operating procedure applies to this alarm?",
    "Are the API recommendations consistent with the maintenance manual?",
]


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(None, pattern=r"^conv-[a-f0-9]{12}$")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": self.formatTime(record), "level": record.levelname, "logger": record.name}
        payload["msg"] = record.getMessage()
        payload.update(getattr(record, "fields", {}))
        return json.dumps(payload, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    for name in ("copilot", "rag", "alarm_api_client"):
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.setLevel(level.upper())
        logger.propagate = False


def load_retriever() -> Retriever | None:
    try:
        return Retriever.from_settings(RagSettings())
    except (FileNotFoundError, ValueError) as exc:
        log.warning("retrieval index unavailable; answers will lack documents", extra={"fields": {"error": str(exc)}})
        return None


def create_app(
    settings: CopilotSettings | None = None,
    service: CopilotService | None = None,
    llm: LlmProvider | None = None,
) -> FastAPI:
    """Production: settings only. Tests: pass a ready CopilotService."""
    settings = settings or CopilotSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if service is not None:
            app.state.service = service
        else:
            configure_logging(settings.log_level)
            token = settings.mcp_auth_token.get_secret_value() if settings.mcp_auth_token else ""
            key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
            app.state.service = CopilotService(
                connector=http_connector(settings.mcp_server_url, token),
                retriever=load_retriever(),
                llm=llm or build_provider(settings.llm_provider, settings.llm_model, key, settings.llm_thinking_level),
                conversations=ConversationStore(settings.max_conversations),
            )
        yield

    app = FastAPI(title="Alarm Investigation Copilot", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST"], allow_headers=["*"]
    )

    @app.middleware("http")
    async def request_log(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id = f"req-{uuid.uuid4().hex[:12]}"
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        log.info(
            "request",
            extra={
                "fields": {
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                }
            },
        )
        return response

    def svc(request: Request) -> CopilotService:
        return request.app.state.service

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(body: ChatRequest, request: Request) -> ChatResponse:
        return await svc(request).ask(body.message.strip(), body.conversation_id)

    @app.get("/api/tools", response_model=ToolsResponse)
    async def tools(request: Request) -> ToolsResponse:
        return await svc(request).list_tools()

    @app.get("/api/conversations/{conversation_id}", response_model=Conversation)
    async def conversation(conversation_id: str, request: Request) -> Conversation:
        conv = svc(request).conversations.get(conversation_id)
        if conv is None:
            raise HTTPException(404, "Conversation not found")
        return conv

    @app.get("/api/examples")
    async def examples() -> dict:
        return {"examples": EXAMPLES}

    @app.get("/api/health")
    async def health(request: Request) -> dict:
        s = svc(request)
        listed = await s.list_tools()
        retriever = s.retriever
        rag = {
            "index_loaded": retriever is not None,
            "chunks": len(retriever.chunks) if retriever else 0,
            "embedder": retriever.embedder.name if retriever else None,
        }
        llm_info = {"provider": "offline"} if s.llm is None else {"provider": s.llm.name}
        ready = listed.connected and retriever is not None
        return {
            "status": "ok" if ready else "degraded",
            "version": __version__,
            "mcp": {
                "server": listed.server,
                "connected": listed.connected,
                "tools": len(listed.tools),
                "error": listed.error,
            },
            "rag": rag,
            "llm": llm_info,
        }

    return app
