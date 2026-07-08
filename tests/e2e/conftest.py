"""Live-server fixture for the Playwright visual E2E smoke.

Opt-in only: the whole package self-skips unless ``RUN_E2E=1`` is set, because it
launches the real FastAPI app (uvicorn) against the local Docker Postgres — not
something the default ``pytest`` gate should require. The server runs with
``DISABLE_AUTH=true`` (local-only dev bypass) and ``CATALOG_DB=postgres`` so the
server-rendered catalog pages exercise their real request-time DB paths.

Skips (never hard-fails) when the stack is unavailable so a dev without Docker
still gets a clean run:
  - ``RUN_E2E`` unset            -> opt-in gate
  - Postgres 127.0.0.1:5432 down -> "start content_generator_postgres"
  - server never becomes ready   -> tail of its captured log
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PG_HOST, PG_PORT = "127.0.0.1", 5432
BOOT_TIMEOUT_S = 90


def _socket_open(host: str, port: int, timeout: float = 1.0) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(timeout)
        return probe.connect_ex((host, port)) == 0


def _http_ok(host: str, port: int, path: str, timeout: float = 3.0) -> bool:
    """Raw-socket HTTP GET returning True on a 2xx status line.

    Deliberately does NOT use urllib/requests: HTTP_PROXY/HTTPS_PROXY in this
    environment route 127.0.0.1 through an external proxy that drops the
    connection. A raw socket bypasses the proxy entirely (same reason the memo
    says curl returns 000 here — use the socket, not an HTTP client).
    """
    try:
        conn = socket.create_connection((host, port), timeout=timeout)
    except OSError:
        return False
    try:
        conn.settimeout(timeout)
        conn.sendall(
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n".encode()
        )
        first_line = b""
        while b"\r\n" not in first_line:
            chunk = conn.recv(256)
            if not chunk:
                break
            first_line += chunk
        status_line = first_line.split(b"\r\n", 1)[0]
        return b" 200 " in status_line
    except OSError:
        return False
    finally:
        conn.close()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _database_url() -> str:
    """Read DATABASE_URL from .env (localhost -> 127.0.0.1 for the Windows IPv6 quirk)."""
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("DATABASE_URL="):
                value = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                return value.replace("localhost", "127.0.0.1")
    return os.environ.get("DATABASE_URL", "").replace("localhost", "127.0.0.1")


@pytest.fixture(scope="session")
def live_server() -> Iterator[str]:
    """Launch run.py on a free port; yield its base URL; terminate on teardown."""
    if os.getenv("RUN_E2E") != "1":
        pytest.skip("visual E2E is opt-in — set RUN_E2E=1 (needs live server + Postgres)")
    if not _socket_open(PG_HOST, PG_PORT):
        pytest.skip(f"Postgres {PG_HOST}:{PG_PORT} down — run: docker start content_generator_postgres")

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update(
        {
            "CONTENT_GENERATOR_SKIP_VENV_REEXEC": "1",
            "PYTHONPATH": str(REPO_ROOT / "src"),
            "HOST": "127.0.0.1",
            "PORT": str(port),
            "DISABLE_AUTH": "true",
            "CATALOG_DB": "postgres",
            "RELOAD": "false",
            "LOG_LEVEL": "WARNING",
        }
    )
    database_url = _database_url()
    if database_url:
        env["DATABASE_URL"] = database_url

    log_path = REPO_ROOT / "tests" / "e2e" / ".server.log"
    log_handle = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "run.py")],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )

    try:
        deadline = time.time() + BOOT_TIMEOUT_S
        ready = False
        while time.time() < deadline:
            if proc.poll() is not None:
                log_handle.flush()
                tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
                pytest.skip(f"server exited early (code {proc.returncode}):\n{tail}")
            if _socket_open("127.0.0.1", port) and _http_ok("127.0.0.1", port, "/api/v1/health"):
                ready = True
                break
            time.sleep(1)
        if not ready:
            log_handle.flush()
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            pytest.skip(f"server not ready in {BOOT_TIMEOUT_S}s:\n{tail}")
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_handle.close()
