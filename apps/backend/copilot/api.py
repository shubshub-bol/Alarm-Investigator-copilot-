"""HTTP API for the GUI.

POST   /api/chat                    ask a question (optionally continuing a conversation)
POST   /api/chat/stream             the same as Server-Sent Events: started, understanding, discovery,
                                    one 'step' per tool call or document search, then 'answer' (or 'error')
GET    /api/conversations           saved chats, newest activity first
GET    /api/conversations/{id}      one saved chat with every answer
PATCH  /api/conversations/{id}      rename a chat
DELETE /api/conversations/{id}      delete a chat and its answers
GET    /api/tools                   MCP tool discovery view (names, descriptions, schemas)
GET    /api/health                  MCP / RAG / LLM / storage readiness
GET    /api/examples                example questions for the GUI
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Path, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, StringConstraints

from copilot import __version__
from copilot.config import CopilotSettings
from copilot.conversations import (
    ConversationDetail,
    ConversationStore,
    ConversationSummary,
    StorageError,
)
from copilot.llm import LlmProvider, build_provider
from copilot.mcp_client import http_connector
from copilot.service import ChatResponse, CopilotService, ToolsResponse
from copilot.suggestions import STARTER_QUESTIONS
from rag.config import RagSettings
from rag.retrieval import Retriever

log = logging.getLogger("copilot.api")


CONVERSATION_ID = r"^conv-[a-f0-9]{12}$"
ConversationId = Annotated[str, Path(pattern=CONVERSATION_ID)]


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(None, pattern=CONVERSATION_ID)


class ConversationList(BaseModel):
    conversations: list[ConversationSummary]


class RenameRequest(BaseModel):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


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
                conversations=ConversationStore(settings.db_path),
            )
        yield

    app = FastAPI(title="Alarm Investigation Copilot", version=__version__, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["*"],
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

    @app.exception_handler(StorageError)
    async def storage_unavailable(request: Request, exc: StorageError) -> JSONResponse:
        log.error("conversation storage failed", extra={"fields": {"path": request.url.path, "error": str(exc)}})
        return JSONResponse(
            {"detail": "Saved chats are unavailable right now. Check the backend logs."}, status_code=503
        )

    @app.post("/api/chat", response_model=ChatResponse)
    async def chat(body: ChatRequest, request: Request) -> ChatResponse:
        return await svc(request).ask(body.message.strip(), body.conversation_id)

    @app.post("/api/chat/stream")
    async def chat_stream(body: ChatRequest, request: Request) -> StreamingResponse:
        service = svc(request)
        queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()
        seen: dict[str, Any] = {}

        async def on_event(event: str, data: dict[str, Any]) -> None:
            if event == "started":
                seen.update(data)
            await queue.put((event, data))

        async def produce() -> None:
            try:
                response = await service.ask(body.message.strip(), body.conversation_id, on_event=on_event)
                await queue.put(("answer", response.model_dump(mode="json")))
            except Exception:  # reported to the browser as an error event; details only in the logs
                log.exception("chat stream failed", extra={"fields": {"trace_id": seen.get("trace_id")}})
                message = (
                    "The investigation failed unexpectedly. Try again; if it keeps failing, check the backend logs."
                )
                await queue.put(("error", {"message": message, "trace_id": seen.get("trace_id")}))
            finally:
                await queue.put(None)

        async def stream() -> AsyncIterator[str]:
            task = asyncio.create_task(produce())
            try:
                while (item := await queue.get()) is not None:
                    event, data = item
                    yield f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
            finally:
                if not task.done():  # the browser went away: stop the investigation
                    task.cancel()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/tools", response_model=ToolsResponse)
    async def tools(request: Request) -> ToolsResponse:
        return await svc(request).list_tools()

    @app.get("/api/conversations", response_model=ConversationList)
    def list_conversations(request: Request) -> ConversationList:
        return ConversationList(conversations=svc(request).conversations.list_conversations())

    @app.get("/api/conversations/{conversation_id}", response_model=ConversationDetail)
    def get_conversation(conversation_id: ConversationId, request: Request) -> ConversationDetail:
        detail = svc(request).conversations.get(conversation_id)
        if detail is None:
            raise HTTPException(404, "Conversation not found")
        return detail

    @app.patch("/api/conversations/{conversation_id}", response_model=ConversationSummary)
    def rename_conversation(
        conversation_id: ConversationId, body: RenameRequest, request: Request
    ) -> ConversationSummary:
        summary = svc(request).conversations.rename(conversation_id, body.title)
        if summary is None:
            raise HTTPException(404, "Conversation not found")
        return summary

    @app.delete("/api/conversations/{conversation_id}", status_code=204)
    def delete_conversation(conversation_id: ConversationId, request: Request) -> Response:
        if not svc(request).conversations.delete(conversation_id):
            raise HTTPException(404, "Conversation not found")
        return Response(status_code=204)

    @app.get("/api/examples")
    async def examples() -> dict:
        return {"examples": STARTER_QUESTIONS}

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
        storage = await asyncio.to_thread(s.conversations.health)
        ready = listed.connected and retriever is not None and storage["ok"]
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
            "storage": storage,
        }

    return app
