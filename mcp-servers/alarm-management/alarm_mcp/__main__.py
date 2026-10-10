"""Run the MCP server on its own.

python -m alarm_mcp            # streamable HTTP on MCP_HOST:MCP_PORT, endpoint /mcp
python -m alarm_mcp --stdio    # stdio, e.g. for MCP Inspector or a desktop MCP client
"""

import argparse
import json
import logging

import uvicorn

from alarm_api_client import AlarmApiClient, ClientSettings
from alarm_mcp.app import create_http_app
from alarm_mcp.config import McpSettings
from alarm_mcp.server import build_server


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": self.formatTime(record), "level": record.levelname, "logger": record.name}
        payload["msg"] = record.getMessage()
        payload.update(getattr(record, "fields", {}))
        return json.dumps(payload)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    for name in ("alarm_mcp", "alarm_api_client"):
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.setLevel(level.upper())
        logger.propagate = False


def main() -> None:
    parser = argparse.ArgumentParser(description="Alarm Management MCP server")
    parser.add_argument("--stdio", action="store_true", help="serve over stdio instead of HTTP")
    args = parser.parse_args()

    settings = McpSettings()
    configure_logging(settings.log_level)
    if args.stdio:
        api_settings = ClientSettings()
        build_server(api_client_factory=lambda: AlarmApiClient(api_settings)).run("stdio")
    else:
        uvicorn.run(create_http_app(settings), host=settings.host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
