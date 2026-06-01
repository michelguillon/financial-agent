"""app.py — FastAPI app for the personal-finance-agent web demo.

Three API routes (session lifecycle + turn streaming) and static-file
serving for the built React bundle. State is in-memory; restart wipes
everything (acceptable for an ephemeral demo).
"""
from __future__ import annotations

import asyncio
import hmac
import os
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
from agent.replay import replay as replay_transcript  # noqa: E402
from agent.tools import _stats_sink  # noqa: E402
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
from web.backend.replays import get_replay, list_replays  # noqa: E402
from web.backend.sessions import SessionManager  # noqa: E402
from web.backend.stats import Stats  # noqa: E402
from web.backend.streaming import (  # noqa: E402
    WebSseRenderer,
    format_sse,
    push_sentinel,
    stream_turn,
)

ADMIN_TOKEN_ENV = "ADMIN_TOKEN"

# Replay pacing — server-side knob, exposed as ?delay=N on the stream route.
DEFAULT_REPLAY_DELAY_SECONDS = 0.8
MAX_REPLAY_DELAY_SECONDS = 5.0

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
    stats = Stats()
    app.state.sessions = sessions
    app.state.rate_limiter = rate_limiter
    app.state.stats = stats
    # C2: wire the agent-tool batch counters into this Stats instance.
    # CLI runs leave the sink unset; the sink no-ops in that case.
    _stats_sink.register(stats)
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
        _stats_sink.reset()


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
    stats: Stats = request.app.state.stats

    client_ip = get_client_ip(request)
    try:
        rate_limiter.check(client_ip)
    except RateLimitedError as e:
        stats.record_rate_limit_rejection()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limited: max {MAX_SESSIONS_PER_IP_PER_DAY} sessions per IP per day.",
            headers={"Retry-After": str(e.retry_after_seconds)},
        )

    ws = await sessions.create(client_ip=client_ip)
    rate_limiter.record(client_ip)
    stats.record_session_created()

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
    stats: Stats = request.app.state.stats
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
        stats.record_turn(kind="budget_blocked")
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
    cost_before = ws.agent_session.cost_usd

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
            failed = False
        except Exception:
            final_text = ""
            failed = True
        ws.turn_count += 1
        cost_delta = max(0.0, ws.agent_session.cost_usd - cost_before)
        stats.record_turn(
            kind="failed" if failed else "completed",
            cost_delta_usd=cost_delta,
        )
        yield format_sse("turn.completed", {
            "final_text": final_text,
            "cumulative_cost_usd": ws.agent_session.cost_usd,
            "budget_remaining_usd": budget_remaining(ws.agent_session.cost_usd),
            "turns_so_far": ws.agent_session.turn_count,
        })

    return _sse_response(event_stream())


@app.get("/api/replays")
def replays_catalogue() -> dict:
    """List the curated transcripts available for the Live/Replay toggle.

    No auth, no rate limit, no session — replay is a public read of bundled
    content and bypasses the cost/rate-limit guards by design.
    """
    return {"replays": list_replays()}


@app.get("/api/replays/{replay_id}/stream")
async def replay_stream(replay_id: str, request: Request, delay: float = DEFAULT_REPLAY_DELAY_SECONDS):
    """SSE stream that walks a bundled transcript through WebSseRenderer.

    Reuses `agent.replay.replay()` from D2 by running it in
    `asyncio.to_thread` — same async-sync bridge the live /turn route
    uses for `run_turn`. The renderer's `show_user_text` callback (added
    when D2 extended the Renderer protocol) emits `user_text` SSE events
    so the browser can render the original user prompts.
    """
    meta = get_replay(replay_id)
    if meta is None or not meta.path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Replay {replay_id!r} not found.",
        )

    # Count the stream start (post-404). Aborts and disconnects shouldn't
    # undercount: "stream started" is the right granularity.
    stats: Stats = request.app.state.stats
    stats.record_replay_started(meta.id)

    # Clamp delay to a sane range; ?delay=0 is allowed (debug/instant).
    delay = max(0.0, min(MAX_REPLAY_DELAY_SECONDS, delay))

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    renderer = WebSseRenderer(queue=queue, loop=loop)

    # Opening event with the metadata the React side needs to label the stream.
    await queue.put({
        "type": "replay.info",
        "data": {
            "replay_id": meta.id,
            "title": meta.title,
            "summary": meta.summary,
            "delay_seconds": delay,
        },
    })

    def _runner() -> None:
        try:
            replay_transcript(
                meta.path, renderer,
                delay_seconds=delay,
                show_header=False,  # the header is `replay.info` above
            )
        finally:
            push_sentinel(queue, loop)

    replay_task = asyncio.create_task(asyncio.to_thread(_runner))

    async def event_stream():
        async for sse_chunk in stream_turn(queue, replay_task, error_where="replay"):
            yield sse_chunk
        # Mirror /turn's turn.completed — gives the React side a clean
        # signal to drop the streaming spinner.
        yield format_sse("replay.completed", {"replay_id": meta.id})

    return _sse_response(event_stream())


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

def _require_admin(request: Request) -> None:
    """Gate admin routes via the ADMIN_TOKEN env var.

    - Env unset → 503 ("admin disabled"). Default posture in dev; the
      endpoint is invisible until the operator opts in.
    - Env set + header missing/wrong → 401.
    Uses hmac.compare_digest for constant-time comparison.
    """
    expected = os.environ.get(ADMIN_TOKEN_ENV)
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Admin disabled. Set {ADMIN_TOKEN_ENV} to enable.",
        )
    provided = request.headers.get("x-admin-token", "")
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Admin-Token.",
        )


@app.get("/admin/stats")
def admin_stats(request: Request) -> dict:
    """Operator-only JSON snapshot of demo activity.

    Counters live on `app.state.stats` (in-memory; restart wipes). See
    web/backend/stats.py for the shape.
    """
    _require_admin(request)
    stats: Stats = request.app.state.stats
    sessions: SessionManager = request.app.state.sessions
    rate_limiter: RateLimiter = request.app.state.rate_limiter
    return stats.to_dict(sessions=sessions, rate_limiter=rate_limiter)


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
