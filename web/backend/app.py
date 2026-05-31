"""app.py — FastAPI app for the personal-finance-agent web demo.

Three API routes (session lifecycle + turn streaming) and static-file
serving for the built React bundle. State is in-memory; restart wipes
everything (acceptable for an ephemeral demo).
"""
from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.agent import run_turn  # noqa: E402
from db.database import SESSION_DB_PATH  # noqa: E402

from web.backend.limits import (  # noqa: E402
    BUDGET_USD,
    BudgetExceededError,
    MAX_SESSIONS_PER_IP_PER_DAY,
    RateLimitedError,
    RateLimiter,
    budget_remaining,
    check_budget,
)
from web.backend.sessions import SessionManager  # noqa: E402
from web.backend.streaming import (  # noqa: E402
    WebSseRenderer,
    format_sse,
    push_sentinel,
    stream_turn,
)

# Static bundle from the Vite build. Optional at dev time — the FastAPI
# app still serves the API even if the frontend hasn't been built yet.
FRONTEND_DIST = PROJECT_ROOT / "web" / "frontend" / "dist"


# ---------------------------------------------------------------------------
# Lifespan: spin up SessionManager + sweeper, tear down on shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    sessions = SessionManager()
    rate_limiter = RateLimiter()
    app.state.sessions = sessions
    app.state.rate_limiter = rate_limiter
    # Pre-build the seed DB so the first user doesn't wait ~2s.
    await sessions._ensure_seed_db()
    sweeper_task = asyncio.create_task(sessions.run_sweeper())
    try:
        yield
    finally:
        sweeper_task.cancel()
        try:
            await sweeper_task
        except asyncio.CancelledError:
            pass
        sessions.shutdown()


app = FastAPI(title="personal-finance-agent demo", lifespan=lifespan)

# CORS: not strictly needed when frontend + backend share an origin (the
# packaged container), but useful for local dev when Vite runs on :5173
# and the backend on :8000.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class CreateSessionResponse(BaseModel):
    session_id: str
    budget_total_usd: float
    budget_used_usd: float
    turns_so_far: int
    sessions_remaining_today: int


class TurnRequest(BaseModel):
    user_text: str = Field(..., min_length=1, max_length=4000)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_client_ip(request: Request) -> str:
    """Resolve the real client IP, trusting X-Forwarded-For when set.

    Convention: the deployment fronts FastAPI with a tunnel/proxy that sets
    X-Forwarded-For honestly (Cloudflare Tunnel does). If the proxy is ever
    swapped, re-evaluate this trust.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        # X-Forwarded-For = "client, proxy1, proxy2"; first entry is the origin.
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health(request: Request) -> dict:
    sessions: SessionManager = request.app.state.sessions
    return {"status": "ok", "active_sessions": sessions.active_count()}


@app.post("/api/sessions", response_model=CreateSessionResponse,
          status_code=status.HTTP_201_CREATED)
async def create_session(request: Request) -> CreateSessionResponse:
    sessions: SessionManager = request.app.state.sessions
    rate_limiter: RateLimiter = request.app.state.rate_limiter

    client_ip = get_client_ip(request)
    try:
        rate_limiter.check(client_ip)
    except RateLimitedError as e:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limited: max {MAX_SESSIONS_PER_IP_PER_DAY} sessions per IP per day.",
            headers={"Retry-After": str(e.retry_after_seconds)},
        )

    ws = await sessions.create(client_ip=client_ip)
    rate_limiter.record(client_ip)

    return CreateSessionResponse(
        session_id=ws.id,
        budget_total_usd=BUDGET_USD,
        budget_used_usd=0.0,
        turns_so_far=0,
        sessions_remaining_today=max(
            0, MAX_SESSIONS_PER_IP_PER_DAY - rate_limiter.used(client_ip),
        ),
    )


@app.delete("/api/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def end_session(session_id: str, request: Request):
    sessions: SessionManager = request.app.state.sessions
    sessions.end(session_id)
    # Idempotent: 204 even if it didn't exist (don't leak which IDs are valid).
    return None


@app.post("/api/sessions/{session_id}/turn")
async def turn(session_id: str, body: TurnRequest, request: Request):
    sessions: SessionManager = request.app.state.sessions
    ws = sessions.get(session_id)
    if ws is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found or expired. Start a new session.",
        )

    # Cost-cap check BEFORE the API call so an over-budget request never spends.
    try:
        check_budget(ws.agent_session.cost_usd)
    except BudgetExceededError as e:
        # Capture values in locals — `except E as e` clears `e` after the
        # block exits, so the generator closure can't reference it directly.
        used = e.used
        budget = e.budget
        async def budget_stream():
            yield format_sse("budget.exceeded", {
                "used_usd": used,
                "budget_usd": budget,
            })
        return _sse_response(budget_stream())

    # Build the SSE-driving primitives.
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    renderer = WebSseRenderer(queue=queue, loop=loop)

    # Push the opening session.info event before starting the agent.
    await queue.put({
        "type": "session.info",
        "data": {
            "session_id": ws.id,
            "budget_total_usd": BUDGET_USD,
            "budget_used_usd": ws.agent_session.cost_usd,
            "turns_so_far": ws.agent_session.turn_count,
        },
    })

    # Run the agent loop in a worker thread. SESSION_DB_PATH is a ContextVar
    # which propagates across asyncio.to_thread automatically.
    SESSION_DB_PATH.set(ws.db_path)

    def _runner() -> str:
        try:
            return run_turn(ws.agent_session, body.user_text, renderer)
        finally:
            # Always sentinel — guarantees the stream generator terminates
            # even when run_turn raises.
            push_sentinel(queue, loop)

    run_turn_task = asyncio.create_task(asyncio.to_thread(_runner))

    async def event_stream():
        async for sse_chunk in stream_turn(queue, run_turn_task):
            yield sse_chunk
        # Sentinel arrives from _runner's `finally`, which executes BEFORE
        # the worker thread fully returns. Await the task so the result is
        # actually available before turn.completed is emitted.
        try:
            final_text = await run_turn_task
        except Exception:
            final_text = ""
        ws.turn_count += 1
        yield format_sse("turn.completed", {
            "final_text": final_text,
            "cumulative_cost_usd": ws.agent_session.cost_usd,
            "budget_remaining_usd": budget_remaining(ws.agent_session.cost_usd),
            "turns_so_far": ws.agent_session.turn_count,
        })

    return _sse_response(event_stream())


def _sse_response(generator) -> StreamingResponse:
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # discourage proxy buffering
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# Static frontend (mounted last so /api routes take priority)
# ---------------------------------------------------------------------------

if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
