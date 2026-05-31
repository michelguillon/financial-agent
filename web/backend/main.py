"""uvicorn entry point.

    python -m web.backend.main
    uvicorn web.backend.app:app --host 0.0.0.0 --port 8000 --workers 1

Workers must be 1 — SessionManager state is per-process in-memory.
"""
from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run(
        "web.backend.app:app",
        host="0.0.0.0",
        port=8000,
        workers=1,
        log_level="info",
    )


if __name__ == "__main__":
    main()
