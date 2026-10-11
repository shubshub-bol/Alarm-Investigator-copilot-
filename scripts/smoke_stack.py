"""Smoke-test a running copilot stack (docker compose, or the four programs started by hand).

    python scripts/smoke_stack.py                                   # the GUI at http://localhost:3000 (Docker)
    python scripts/smoke_stack.py --base-url http://localhost:5173  # the Vite dev server

Checks that every service is healthy, then asks the assignment's acceptance question through the GUI's
/api route and checks the parts an evaluator looks for: asset resolution through MCP, a multi-step tool
chain with one trace id, document citations, and the conflict check. Standard library only, so it runs
anywhere Python 3.12 does. Exits 1 on the first failed check.
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

ACCEPTANCE = (
    "Investigate recurring high-severity alarms for Boiler Feed Pump 101 over the last 90 days, identify likely "
    "contributing factors, retrieve the relevant operating procedure, and provide recommended actions with source "
    "evidence."
)


def request(url: str, body: dict[str, Any] | None = None, timeout: float = 10) -> dict[str, Any]:
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"not an http(s) URL: {url}")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})  # noqa: S310 - scheme checked above
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - scheme checked above
        return json.loads(resp.read())


class Checks:
    def __init__(self) -> None:
        self.failed = False

    def check(self, ok: bool, what: str) -> None:
        print(("ok    " if ok else "FAIL  ") + what)
        self.failed = self.failed or not ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:3000", help="where the GUI and its /api route are")
    parser.add_argument("--api-url", default="http://localhost:8000", help="the Alarm API simulator")
    parser.add_argument("--mcp-url", default="http://localhost:9000", help="the MCP server (without /mcp)")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    c = Checks()

    try:
        api = request(f"{args.api_url.rstrip('/')}/health")
        c.check(api.get("status") == "ok", f"Alarm API simulator is healthy ({api.get('data')})")
        mcp = request(f"{args.mcp_url.rstrip('/')}/health")
        c.check(mcp.get("status") == "ok", "MCP server is healthy")
        health = request(f"{base}/api/health")
        c.check(health["mcp"]["connected"] and health["mcp"]["tools"] == 13, "backend reaches the MCP server: 13 tools")
        c.check(health["rag"]["index_loaded"] and health["rag"]["chunks"] > 0, "document index is loaded")
        c.check(health.get("storage", {}).get("ok", True), "saved chats are available")
        print(f"      language model: {health['llm']['provider']}")

        started = time.perf_counter()
        body = request(f"{base}/api/chat", {"message": ACCEPTANCE}, timeout=300)
        print(f"      acceptance question answered in {time.perf_counter() - started:.1f} s")
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        c.check(False, f"stack is reachable ({exc})")
        return 1

    tools = [s for s in body["trace"] if s["kind"] == "mcp_tool"]
    searches = [s for s in body["trace"] if s["kind"] == "rag_retrieval"]
    labels = {cit["label"] for cit in body["answer"]["citations"]}
    c.check(body["status"] == "ok", "answer status is ok")
    c.check(bool(tools) and tools[0]["tool"] == "search_assets", "the asset is resolved through search_assets")
    c.check(body["evidence"]["asset"]["asset_id"] == "AST-BFP-101", "resolved to AST-BFP-101")
    c.check(len(tools) >= 8 and all(s["status"] == "ok" for s in tools), f"{len(tools)} MCP tool calls, all ok")
    c.check(
        all(s["result"]["meta"]["trace_id"] == body["trace_id"] for s in tools),
        "every tool call carries one trace id",
    )
    c.check(bool(searches) and all(s["status"] == "ok" for s in searches), f"{len(searches)} document searches, all ok")
    c.check(
        any(label.startswith("SOP-BFP-001") for label in labels),
        f"cites the operating procedure ({len(labels)} citations)",
    )
    c.check(
        any(a["verdict"] == "conflict" for a in body["answer"]["action_assessments"]),
        "flags the Alarm API advice that conflicts with the procedure",
    )
    print("\nALL CHECKS PASSED" if not c.failed else "\nSOME CHECKS FAILED")
    return 1 if c.failed else 0


if __name__ == "__main__":
    sys.exit(main())
