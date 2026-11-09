"""Parsing of uploaded files in a separate process with memory and CPU limits.

A 10 KB PDF whose stream inflates to hundreds of megabytes, or a 5 KB XLSX that declares a million empty rows, is a
valid file to the parser: only limits stop it. Parsed in the server process, one such upload took the whole service
down (1 GB container) with every running preparation of every user (audit 30.09.2026). In a child process the limit
hits the child: the upload gets a readable refusal, the service keeps running. The limits are outside the model and
outside the parser — the book's «sandbox isolation» guardrail on the execution layer.

The child is a fresh interpreter (`python -m project_generator_lab.core.isolated`), not a fork of the server: it
inherits no threads, event loop or sockets, and it does not re-run the server's main module. It sets its own limits
before it imports the parser. On Windows (local development) there are no rlimits; the file is parsed in-process.
"""

from __future__ import annotations

import importlib
import os
import pickle
import subprocess
import sys
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

MEMORY_MB = 600  # the service itself uses ~150 MB of the container's 1 GB
CPU_SECONDS = 30
# One heavy parse at a time: two children at the memory limit would not fit the container together. A file that waits
# longer than QUEUE_SECONDS is refused rather than queued without end.
SLOTS = threading.BoundedSemaphore(1)
QUEUE_SECONDS = 45
# Its own threads: waiting uploads must not occupy the default pool that DNS checks of links and other work use
# (independent review 30.09.2026: queued uploads delayed an unrelated call by seconds).
POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="isolated-parse")


def _own_pool() -> None:
    """A pool inherited through fork remembers idle threads that the new process does not have: work given to it
    waits forever. The server is never forked; the mutation run forks a worker per mutant, and every worker that
    reached an upload over HTTP hung there until its timeout (found 05.10.2026 with py-spy)."""
    global POOL
    POOL = ThreadPoolExecutor(max_workers=3, thread_name_prefix="isolated-parse")


if hasattr(os, "register_at_fork"):  # not on Windows
    os.register_at_fork(after_in_child=_own_pool)
# The child gets only what an interpreter needs: never the API keys of the server.
PASSED_ENV = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "HOME", "TMPDIR", "SYSTEMROOT", "PYTHONIOENCODING", "PYTHONDONTWRITEBYTECODE")
BUSY = "Сейчас разбирается другой файл. Загрузите этот ещё раз через минуту."
ROOT = Path(__file__).resolve().parents[2]  # the directory that contains the project_generator_lab package


class ParseRefused(Exception):
    """The file could not be parsed within the limits; the text is for the methodologist."""


HEAVY = "Файл слишком тяжёлый для разбора. Сохраните нужный фрагмент отдельно и загрузите его."
SLOW = "Файл обрабатывается слишком долго. Сохраните нужный фрагмент отдельно и загрузите его."
BIG = "Файл разворачивается в слишком большой объём данных. Сохраните нужные страницы или лист отдельно."


def available() -> bool:
    try:
        import resource  # noqa: F401
    except ImportError:
        return False
    return True


def run(fn: Callable[..., Any], *args: Any, memory_mb: int = MEMORY_MB, cpu_seconds: int = CPU_SECONDS) -> Any:
    """fn(*args) in a child process under the limits; its own exceptions are re-raised here unchanged."""
    if not available():
        return fn(*args)
    job = pickle.dumps((fn.__module__, fn.__qualname__, args, memory_mb, cpu_seconds))
    env = {key: os.environ[key] for key in PASSED_ENV if key in os.environ}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT), os.environ.get("PYTHONPATH", "")]))
    if not SLOTS.acquire(timeout=QUEUE_SECONDS):
        raise ParseRefused(BUSY)
    try:
        done = subprocess.run([sys.executable, "-m", "project_generator_lab.core.isolated"], input=job,
                              capture_output=True, timeout=cpu_seconds + 30, cwd=ROOT, env=env, check=False)
    except subprocess.TimeoutExpired:
        raise ParseRefused(SLOW) from None
    finally:
        SLOTS.release()
    if done.returncode != 0 or not done.stdout:
        # Killed by a limit before it could answer: CPU time (SIGXCPU) or memory inside a C extension.
        raise ParseRefused(HEAVY)
    status, value = pickle.loads(done.stdout)
    if status == "ok":
        return value
    if status == "refused":
        raise ParseRefused(value)
    raise value


def main() -> None:
    import resource

    module, name, args, memory_mb, cpu_seconds = pickle.loads(sys.stdin.buffer.read())
    # The answer travels on stdout: anything a parser prints goes to stderr instead of corrupting it.
    answer = sys.stdout.buffer
    sys.stdout = sys.stderr
    limit = memory_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
    try:
        fn = getattr(importlib.import_module(module), name)
        result: tuple[str, Any] = ("ok", fn(*args))
    except MemoryError:
        result = ("refused", BIG)
    except Exception as error:  # re-raised in the server as it is: InputError keeps its text for the methodologist
        result = ("error", error)
    answer.write(pickle.dumps(result))
    answer.flush()


if __name__ == "__main__":
    main()
