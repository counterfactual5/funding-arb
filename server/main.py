#!/usr/bin/env python3
"""FastAPI backend for funding-arb trading dashboard."""

from __future__ import annotations

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

# ---------------------------------------------------------------------------
# Make the project root and scripts/ importable so we can use existing
# trading modules regardless of how the server is launched
# (python server/main.py, python -m server.main, or uvicorn server.main:app).
# ---------------------------------------------------------------------------
_ROOT_DIR = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _ROOT_DIR / "scripts"
for _p in (str(_ROOT_DIR), str(_SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
from server.routes import backtest, positions, scanner, settings  # noqa: E402

# ---------------------------------------------------------------------------
# WebSocket connection manager
# ---------------------------------------------------------------------------


class ConnectionManager:
    """Simple fan-out WebSocket manager for real-time event push."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        try:
            self._connections.remove(ws)
        except ValueError:
            pass

    async def broadcast(self, event: str, data: dict) -> None:
        msg = json.dumps({"event": event, "data": data})
        dead: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


async def _background_scanner_loop() -> None:
    """Periodically run the spread scanner and broadcast results via WS."""
    from server.routes.scanner import _scan_pure_fn, scanner_trigger  # noqa: E402
    from server.routes.settings import _strategy_config  # noqa: E402

    if _scan_pure_fn is None:
        print(
            "[scanner] pure futures scanner unavailable, background loop disabled",
            flush=True,
        )
        return

    print("[scanner] background loop started", flush=True)

    async def _warm(strategy: str) -> None:
        try:
            r = await scanner_trigger(strategy=strategy)
            if not r.get("success"):
                print(f"[scanner] {strategy} scan: {r.get('error')}", flush=True)
        except Exception as e:
            print(f"[scanner] {strategy} scan failed: {e}", flush=True)

    # Run initial scans at startup so all three tabs have data on first connect.
    # Pure first (fast, most viewed), then carry/unified warm up concurrently.
    await _warm("pure")
    warmup = asyncio.create_task(asyncio.gather(_warm("carry"), _warm("unified")))

    cycle = 0
    while True:
        try:
            interval = int(_strategy_config.get("scan_interval_sec", 300) or 300)
            await asyncio.sleep(max(30, interval))
            await _warm("pure")
            # Carry/unified are heavier — refresh them every other cycle
            cycle += 1
            if cycle % 2 == 0:
                asyncio.create_task(_warm("carry"))
                asyncio.create_task(_warm("unified"))
        except asyncio.CancelledError:
            warmup.cancel()
            break
        except Exception as e:
            print(f"[scanner] background scan failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"[funding-arb server] scripts dir: {_SCRIPTS_DIR}")
    # Load exchange credentials (keyring / age / json) into os.environ so
    # settings endpoints and live executors see them.
    try:
        from core.credentials import ensure_env  # noqa: E402

        ensure_env()
    except Exception as e:
        print(f"[credentials] ensure_env failed: {e}")
    task = asyncio.create_task(_background_scanner_loop())

    def _log_task_crash(t: asyncio.Task) -> None:
        if not t.cancelled() and t.exception() is not None:
            print(f"[scanner] background loop crashed: {t.exception()!r}", flush=True)

    task.add_done_callback(_log_task_crash)
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        print("[funding-arb server] shutting down")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="funding-arb API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "http://localhost:5173",
        "tauri://localhost",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(scanner.router, prefix="/api")
app.include_router(positions.router, prefix="/api")
app.include_router(backtest.router, prefix="/api")
app.include_router(settings.router, prefix="/api")


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@app.websocket("/ws/events")
async def ws_events(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            try:
                text = await asyncio.wait_for(ws.receive_text(), timeout=60)
                if text == "ping":
                    await ws.send_text("pong")
            except asyncio.TimeoutError:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(ws)


async def push_event(event: str, data: dict) -> None:
    """Broadcast an event to all connected WebSocket clients."""
    await manager.broadcast(event, data)


# ---------------------------------------------------------------------------
# Static file serving (Browser mode)
# ---------------------------------------------------------------------------

_WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


def _mount_static(app: FastAPI, dist_dir: Path) -> None:
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    app.mount(
        "/assets", StaticFiles(directory=dist_dir / "assets"), name="static-assets"
    )

    @app.get("/{path:path}")
    async def spa_fallback(path: str):
        """SPA fallback: Any unknown path returns index.html for Vue Router to handle."""
        file = dist_dir / path
        if file.is_file():
            return FileResponse(file)
        return FileResponse(dist_dir / "index.html")


if _WEB_DIST.exists() and (_WEB_DIST / "index.html").exists():
    _mount_static(app, _WEB_DIST)
    _HAS_WEB_UI = True
else:
    _HAS_WEB_UI = False


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Funding Arb Dashboard")
    parser.add_argument(
        "--port", type=int, default=8787, help="Port number (default: 8787)"
    )
    parser.add_argument(
        "--host", default="0.0.0.0", help="Listen address (default: 0.0.0.0)"
    )
    parser.add_argument("--no-reload", action="store_true", help="Disable hot reload")
    args = parser.parse_args()

    mode_info = "Desktop + Web UI" if _HAS_WEB_UI else "API Only (Web UI not built)"
    print(f"\n  Funding Arb Dashboard")
    print(f"  Mode: {mode_info}")
    print(f"  URL: http://{args.host}:{args.port}")
    if _HAS_WEB_UI:
        print(f"  Open the above URL in your browser to use the dashboard")
    else:
        print(f"  Hint: cd web && npm run build to build the Web UI")
    print()

    import uvicorn

    uvicorn.run(
        "server.main:app",
        host=args.host,
        port=args.port,
        reload=not args.no_reload,
        reload_dirs=[str(Path(__file__).resolve().parent)],
    )
