"""Скрипт для запуска FastAPI сервера."""

import logging
import os
import socket
import sys


def _ensure_project_venv() -> None:
    """Restart through the project virtualenv when the shell uses a global Python."""
    if os.getenv("CONTENT_GENERATOR_SKIP_VENV_REEXEC") == "1":
        return

    project_root = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(project_root, ".venv", "Scripts", "python.exe"),
        os.path.join(project_root, ".venv", "bin", "python"),
    ]
    venv_python = next((path for path in candidates if os.path.exists(path)), None)
    if not venv_python:
        return

    current_python = os.path.normcase(os.path.abspath(sys.executable))
    target_python = os.path.normcase(os.path.abspath(venv_python))
    if current_python == target_python:
        return

    os.environ["CONTENT_GENERATOR_SKIP_VENV_REEXEC"] = "1"
    os.execv(venv_python, [venv_python, *sys.argv])


def _can_bind_port(host: str, port: int) -> bool:
    """Check port availability before importing the FastAPI application."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


if __name__ == "__main__":
    _ensure_project_venv()

    import uvicorn
    from dotenv import load_dotenv

    # Загружаем переменные окружения
    load_dotenv()

    # Настраиваем логирование ДО запуска uvicorn
    # Это гарантирует, что логи будут видны в терминале
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(message)s",
        stream=sys.stderr,
        force=True  # Перезаписываем существующую конфигурацию
    )

    # Получаем настройки из переменных окружения
    host = os.getenv("HOST", "127.0.0.1")  # Изменено на 127.0.0.1 для локального доступа
    port = int(os.getenv("PORT", "8000"))
    # По умолчанию reload=False для production (можно включить через RELOAD=true для разработки)
    reload = os.getenv("RELOAD", "false").lower() == "true"

    # Определяем URL для документации
    if host == "0.0.0.0":
        docs_host = "localhost"
    else:
        docs_host = host

    if not _can_bind_port(host, port):
        print(
            f"❌ Порт {port} уже занят для {host}. Сервер не запущен повторно.",
            file=sys.stderr,
            flush=True,
        )
        print(
            f"   Проверьте текущий процесс: http://{docs_host}:{port}/api/v1/health",
            file=sys.stderr,
            flush=True,
        )
        print(
            "   Либо остановите старый сервер, либо задайте другой порт: $env:PORT='8001'; python run.py",
            file=sys.stderr,
            flush=True,
        )
        sys.exit(1)

    print(f"🚀 Запуск FastAPI сервера на http://{host}:{port}", file=sys.stderr, flush=True)
    print(f"📝 Режим разработки (reload): {reload}", file=sys.stderr, flush=True)
    print(f"📚 Документация API: http://{docs_host}:{port}/docs", file=sys.stderr, flush=True)
    print(f"🔍 Альтернативная документация: http://{docs_host}:{port}/redoc", file=sys.stderr, flush=True)
    print(f"💚 Health check: http://{docs_host}:{port}/api/v1/health", file=sys.stderr, flush=True)
    print("💡 Для остановки нажмите Ctrl+C", file=sys.stderr, flush=True)

    try:
        # Используем uvicorn.run с настройками для логирования
        # log_level должен соответствовать уровню root logger
        uvicorn.run(
            "api.main:app",
            host=host,
            port=port,
            reload=reload,  # False по умолчанию для production, можно включить через RELOAD=true
            log_level=log_level.lower(),  # Используем тот же уровень, что и root logger
            access_log=True,  # Включаем access logs
            use_colors=True,  # Включаем цвета в логах
            # Улучшенная обработка завершения
            timeout_keep_alive=5,
            timeout_graceful_shutdown=10
        )
    except KeyboardInterrupt:
        print("\n✅ Сервер остановлен", file=sys.stderr, flush=True)
        # Принудительно завершаем процесс
        os._exit(0)
    except Exception as e:
        print(f"\n❌ Ошибка при запуске сервера: {e}", file=sys.stderr, flush=True)
        os._exit(1)
