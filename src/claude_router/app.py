import json
import logging
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from .config import Config
from .decision import CLOUD, LOCAL, Decider
from .messages import latest_user_text, session_id, to_local
from .sessions import SessionStore

log = logging.getLogger("claude_router")

ROUTED_PATHS = ("v1/messages", "v1/messages/count_tokens")
# 轉送時不帶過去的 header；accept-encoding 改成 identity，串流才能原樣轉手
DROP_REQUEST_HEADERS = {"host", "content-length", "connection", "accept-encoding"}
DROP_RESPONSE_HEADERS = {"content-length", "transfer-encoding", "connection", "content-encoding"}


def create_app(config: Config | None = None) -> FastAPI:
    config = config or Config.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10)) as client:
            app.state.client = client
            app.state.decider = Decider(config, client)
            yield

    app = FastAPI(lifespan=lifespan)
    sessions = SessionStore(config.state_file)
    config.log_file.parent.mkdir(parents=True, exist_ok=True)

    def write_log(entry: dict) -> None:
        entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with config.log_file.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    async def choose_route(request: Request, body: dict) -> tuple[str, str, str | None]:
        sid = session_id(request.headers, body)
        if sessions.is_local(sid):
            return LOCAL, "sticky", sid
        text = latest_user_text(body)
        if text is None:
            return CLOUD, "no-user-text", sid
        if not config.local_url:
            return CLOUD, "no-local-backend", sid
        route, reason = await request.app.state.decider.classify(text)
        if route == LOCAL:
            sessions.lock(sid, reason)
        return route, reason, sid

    async def stream_upstream(client: httpx.AsyncClient, req: httpx.Request) -> Response:
        upstream = await client.send(req, stream=True)
        headers = {k: v for k, v in upstream.headers.items() if k.lower() not in DROP_RESPONSE_HEADERS}
        return StreamingResponse(
            upstream.aiter_raw(),
            status_code=upstream.status_code,
            headers=headers,
            background=BackgroundTask(upstream.aclose),
        )

    @app.get("/_router/sessions")
    async def list_sessions():
        return sessions.locked

    @app.delete("/_router/sessions/{sid}")
    async def unlock_session(sid: str):
        return {"unlocked": sessions.unlock(sid)}

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    async def proxy(path: str, request: Request):
        client: httpx.AsyncClient = request.app.state.client
        raw = await request.body()
        entry: dict = {"method": request.method, "path": path}

        if request.method == "POST" and path in ROUTED_PATHS:
            body = json.loads(raw)
            route, reason, sid = await choose_route(request, body)
            entry.update(route=route, reason=reason, session=sid, model=body.get("model"))
            if config.log_bodies:
                entry["headers"] = {k: v for k, v in request.headers.items() if k.lower() not in ("authorization", "x-api-key")}
                entry["metadata"] = body.get("metadata")
                dump = config.log_file.parent / "bodies" / f"{time.time_ns()}-{route}.json"
                dump.parent.mkdir(exist_ok=True)
                dump.write_text(json.dumps(body, ensure_ascii=False, indent=1))
            write_log(entry)

            if route == LOCAL:
                headers = {"content-type": "application/json", "accept-encoding": "identity"}
                if config.local_api_key:
                    headers["authorization"] = f"Bearer {config.local_api_key}"
                req = client.build_request(
                    "POST",
                    f"{config.local_url}/{path}",
                    headers=headers,
                    content=json.dumps(to_local(body, config.local_model)),
                )
                try:
                    return await stream_upstream(client, req)
                except httpx.HTTPError as exc:
                    # 本地掛了也絕不改送雲端，直接回錯誤讓使用者知道
                    log.error("local backend failed: %r", exc)
                    return JSONResponse(
                        {"type": "error", "error": {"type": "api_error", "message": f"claude-router: local backend unreachable ({exc!r})"}},
                        status_code=502,
                    )
        else:
            write_log(entry | {"route": CLOUD, "reason": "passthrough"})

        headers = {k: v for k, v in request.headers.items() if k.lower() not in DROP_REQUEST_HEADERS}
        headers["accept-encoding"] = "identity"
        url = f"{config.cloud_url}/{path}"
        if request.url.query:
            url += f"?{request.url.query}"
        req = client.build_request(request.method, url, headers=headers, content=raw)
        return await stream_upstream(client, req)

    return app
