"""The S15Code service: one FastAPI app, one importable package.

This process owns graph/memory/document/A2A task semantics, the generative-UI
surface, budget-aware planning and trace export. The gateway process owns models,
keys, routing, quotas and provider quirks. The two share no Python imports and no
database file; the only seam is HTTP.
"""
from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

from s15code import a2a_routes, routes  # noqa: E402
from s15code.core.a2a.official import OfficialA2AServer  # noqa: E402
from s15code.core.a2a.server import A2ADemoServer  # noqa: E402
from s15code.core.a2a.trust import sign_card  # noqa: E402
from s15code.core.memory import MemoryScope  # noqa: E402
from s15code.gateway import GatewayClient  # noqa: E402
from s15code.runtime import AgentRuntime  # noqa: E402
from s15code.ui.routes import router as ui_router  # noqa: E402

PORT = int(os.getenv("S15_PORT", "8113"))


def _secrets(name: str) -> set[str]:
    return {item.strip() for item in os.getenv(name, "").split(",") if item.strip()}


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.gateway = GatewayClient()
    app.state.runtime = AgentRuntime()
    bearers, api_keys = _secrets("S15_A2A_BEARER_TOKENS"), _secrets("S15_A2A_API_KEYS")
    base_url = os.getenv("S15_BASE_URL", f"http://127.0.0.1:{PORT}").rstrip("/")
    card = {
        "name": "S15 live-agent runtime",
        "description": "Outcome-driven graph, scoped memory, semantic indexing and A2A delegation",
        "version": "0.1.0",
        "supportedInterfaces": [
            {"url": f"{base_url}/a2a", "protocolBinding": "JSONRPC", "protocolVersion": "1.0"},
            {"url": f"dns:///127.0.0.1:{int(os.getenv('S15_A2A_GRPC_PORT', '8114'))}",
             "protocolBinding": "GRPC", "protocolVersion": "1.0"},
        ],
        "capabilities": {"streaming": True, "pushNotifications": True},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "skills": [{"id": "grounded-answer", "name": "Grounded answer",
                    "description": "Runs a live graph over explicitly scoped evidence.",
                    "tags": ["live-graph", "memory", "semantic-chunking", "budget-aware"]}],
    }
    if bearers:
        card.setdefault("securitySchemes", {})["bearer"] = {
            "httpAuthSecurityScheme": {"description": "A2A bearer token", "scheme": "bearer"}}
        card.setdefault("securityRequirements", []).append({"schemes": {"bearer": {"list": []}}})
    if api_keys:
        card.setdefault("securitySchemes", {})["apiKey"] = {
            "apiKeySecurityScheme": {"description": "A2A API key", "location": "header", "name": "X-API-Key"}}
        card.setdefault("securityRequirements", []).append({"schemes": {"apiKey": {"list": []}}})
    if key_file := os.getenv("S15_A2A_PRIVATE_KEY_FILE"):
        card = sign_card(card, Path(key_file).read_bytes(), kid=os.getenv("S15_A2A_SIGNING_KID", "s15-local"))

    async def handle_a2a_task(text: str) -> str:
        result = await app.state.runtime.run(
            prompt=text, scope=MemoryScope("a2a", "inbound", "remote-agent", "s15code"),
            llm=lambda prompt, system: app.state.gateway.complete(prompt, system),
            source_uri="a2a://inbound/task", source_author="remote-agent",
        )
        if result["status"] != "completed":
            raise RuntimeError("live graph completed without an answer")
        return result["answer"]

    data_dir = app.state.runtime.root
    push_http = httpx.AsyncClient(timeout=10)
    app.state.a2a_push_http = push_http
    app.state.a2a_server = A2ADemoServer(
        card, task_handler=handle_a2a_task, task_db=data_dir / "a2a.sqlite",
        bearer_tokens=bearers, api_keys=api_keys,
        push_signing_secret=os.getenv("S15_A2A_PUSH_SIGNING_SECRET"), push_http=push_http,
    )
    await app.state.a2a_server.start()
    app.state.a2a_grpc_server = None
    if os.getenv("S15_A2A_GRPC_ENABLED", "1").lower() not in {"0", "false", "no"}:
        app.state.a2a_grpc_server = OfficialA2AServer(
            app.state.a2a_server, data_dir / "a2a.sqlite",
            address=f"127.0.0.1:{int(os.getenv('S15_A2A_GRPC_PORT', '8114'))}",
            bearer_tokens=bearers, api_keys=api_keys,
        )
        await app.state.a2a_grpc_server.start()
    app.state.started_at = time.time()
    yield
    if app.state.a2a_grpc_server:
        await app.state.a2a_grpc_server.stop()
    await app.state.a2a_server.close()
    await push_http.aclose()
    app.state.runtime.close()
    await app.state.gateway.close()


app = FastAPI(title="S15Code — Live Graph, Memory, Semantic Chunking and A2A", lifespan=lifespan)
app.include_router(routes.router)
app.include_router(a2a_routes.router)
app.include_router(ui_router)


@app.get("/healthz")
async def healthz(request: Request):
    return {"ok": True, "service": "s15code", "port": PORT,
            "glc_base_url": request.app.state.gateway.base_url}


@app.get("/readyz")
async def readyz(request: Request):
    try:
        gateway = await request.app.state.gateway.health()
    except Exception as error:
        raise HTTPException(503, f"GLC is unavailable: {type(error).__name__}: {error}") from error
    return {"ok": True, "glc": gateway}
