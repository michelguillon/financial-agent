"""sessions.py — per-visitor session lifecycle for the web demo.

Each visitor gets an isolated SQLite DB so concurrent classification flows
don't collide. The seed DB (synthetic transactions + canonical rules) is
built once per server lifetime and copied per-session — ~30ms vs ~2s for a
full ingest, so session creation feels instant.

State is in-memory only; a server restart wipes all live sessions. The
demo is ephemeral by design (recruiter clicks, plays, leaves).
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.agent import Session as AgentSession  # noqa: E402
from db.database import open_db  # noqa: E402
from db.migrate import ingest  # noqa: E402
from db.seed_rules import seed as seed_rules  # noqa: E402

SYNTHETIC_CSV = PROJECT_ROOT / "data" / "synthetic" / "transactions_synthetic.csv"

IDLE_TIMEOUT_SECONDS = 30 * 60          # 30min idle → evict
SWEEPER_INTERVAL_SECONDS = 5 * 60       # check every 5min


@dataclass
class WebSession:
    id: str
    agent_session: AgentSession
    db_path: Path
    created_at: datetime
    last_active_at: datetime
    client_ip: str = ""
    turn_count: int = 0  # tracked separately from agent_session in case we ever throttle


class SessionManager:
    """In-memory store. Builds the seed DB lazily on first use."""

    def __init__(self, idle_timeout_seconds: int = IDLE_TIMEOUT_SECONDS):
        self._sessions: dict[str, WebSession] = {}
        self._seed_db_path: Path | None = None
        self._idle_timeout = idle_timeout_seconds
        self._tmp_root = Path(tempfile.gettempdir()) / "agent-sessions"
        self._tmp_root.mkdir(exist_ok=True)

    # ------------------------------------------------------------------ seed

    def _build_seed_db(self, path: Path) -> None:
        """Ingest synthetic CSV + seed classifier rules into `path`."""
        with open_db(path) as conn:
            ingest(SYNTHETIC_CSV, conn, source_default="synthetic", replace=False)
            seed_rules(conn)

    async def _ensure_seed_db(self) -> Path:
        if self._seed_db_path is None:
            path = self._tmp_root / "seed.db"
            if path.exists():
                path.unlink()  # stale from a previous server lifetime
            await asyncio.to_thread(self._build_seed_db, path)
            self._seed_db_path = path
        return self._seed_db_path

    # ---------------------------------------------------------------- lifecycle

    async def create(self, client_ip: str = "") -> WebSession:
        seed_path = await self._ensure_seed_db()
        session_id = uuid.uuid4().hex
        session_dir = self._tmp_root / session_id
        session_dir.mkdir(exist_ok=True)
        db_path = session_dir / "finance.db"
        await asyncio.to_thread(shutil.copy, seed_path, db_path)

        now = datetime.now(timezone.utc)
        ws = WebSession(
            id=session_id,
            agent_session=AgentSession(data_source="synthetic"),
            db_path=db_path,
            created_at=now,
            last_active_at=now,
            client_ip=client_ip,
        )
        self._sessions[session_id] = ws
        return ws

    def get(self, session_id: str) -> WebSession | None:
        ws = self._sessions.get(session_id)
        if ws is None:
            return None
        ws.last_active_at = datetime.now(timezone.utc)
        return ws

    def end(self, session_id: str) -> bool:
        ws = self._sessions.pop(session_id, None)
        if ws is None:
            return False
        try:
            shutil.rmtree(ws.db_path.parent, ignore_errors=True)
        except Exception:
            pass
        return True

    def active_count(self) -> int:
        return len(self._sessions)

    # --------------------------------------------------------------- sweeper

    def sweep_idle(self) -> int:
        threshold = datetime.now(timezone.utc) - timedelta(seconds=self._idle_timeout)
        stale = [sid for sid, ws in self._sessions.items() if ws.last_active_at < threshold]
        for sid in stale:
            self.end(sid)
        return len(stale)

    async def run_sweeper(self, interval_seconds: int = SWEEPER_INTERVAL_SECONDS) -> None:
        """Background task: periodically evict idle sessions."""
        while True:
            await asyncio.sleep(interval_seconds)
            self.sweep_idle()

    def shutdown(self) -> None:
        """Clean up all sessions + the seed DB. Called from app lifespan."""
        for sid in list(self._sessions):
            self.end(sid)
        if self._seed_db_path and self._seed_db_path.exists():
            try:
                self._seed_db_path.unlink()
            except Exception:
                pass
