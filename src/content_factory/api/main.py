"""Главное FastAPI приложение."""

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from content_factory.api.db.session import get_database_status, init_db, should_auto_create_tables
from content_factory.api.integrations.auth_cookie import ToolAuthCookieMiddleware
from content_factory.catalog.viewer.app import STATIC_DIR as CATALOG_STATIC_DIR
from content_factory.catalog.web.routers import catalog_admin as catalog_admin_ui
from content_factory.catalog.web.routers import intake as catalog_intake_ui
from content_factory.catalog.web.routers import pages as catalog_pages
from content_factory.catalog.web.routers import reviews as catalog_reviews_ui
from content_factory.catalog.web.routers import up as catalog_up_ui
from content_factory.api.middleware.activity_tracking import ActivityTrackingMiddleware
from content_factory.api.middleware.request_logging import RequestLoggingMiddleware
from content_factory.api.routers import (
    admin,
    auditor,
    auth,
    curriculum,
    download,
    excel_parser,
    generation,
    health,
    metrics,
    readme_check,
    readme_improvement,
    readme_translate,
    regeneration,
    thematic_blocks,
)
from content_factory.api.utils.logger import setup_logging

# Настраиваем логирование
logger = setup_logging(level=os.getenv("LOG_LEVEL", "INFO"))

# Инициализация rate limiter
limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Run application startup/shutdown hooks without deprecated on_event decorators."""
    await startup_event()
    try:
        yield
    finally:
        await shutdown_event()


app = FastAPI(
    title="Content Generator API",
    version="1.0.0",
    description="API для генерации учебного контента для Школы 21",
    lifespan=lifespan,
)

# Добавляем rate limiter в app state
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Сжимаем HTML, CSS, JS и JSON-ответы. Это особенно заметно на страницах с крупной
# статикой и уменьшает время до первого полностью оформленного экрана.
app.add_middleware(GZipMiddleware, minimum_size=1024)

app.add_middleware(SlowAPIMiddleware)

# Добавляем middleware для логирования запросов
# Важно: должен быть после rate limiting, но до других middleware
app.add_middleware(RequestLoggingMiddleware)

# Добавляем middleware для отслеживания активности пользователей
# Обновляем активность не чаще чем раз в минуту
update_interval = int(os.getenv("ACTIVITY_UPDATE_INTERVAL_SECONDS", "60"))
app.add_middleware(ActivityTrackingMiddleware, update_interval_seconds=update_interval)

# Browser-mounted tools use normal navigation, so they receive the shared auth cookie.
app.add_middleware(
    ToolAuthCookieMiddleware,
    protected_prefixes=("/app/auditor", "/app/check", "/app/curriculum", "/app/spravochnik"),
)

# CORS (настраивается через переменные окружения)
# Для ПК-версии: по умолчанию разрешаем только локальные адреса
default_cors_origins = "http://localhost:8000,http://127.0.0.1:8000,http://localhost:3000,http://127.0.0.1:3000"
cors_origins = os.getenv("CORS_ORIGINS", default_cors_origins).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Статические файлы (UI)
static_dir = Path("static")
if static_dir.exists():
    static_path = static_dir.absolute()
    logger.info(f"📁 Статические файлы: {static_path}")

    # Определяем роуты для HTML страниц ПЕРЕД mount статических файлов
    @app.get("/")
    async def read_root():
        """Главная страница с UI."""
        login_path = static_path / "login.html"
        logger.debug(f"🔍 Запрос главной страницы, путь: {login_path}")
        if not login_path.exists():
            logger.error(f"❌ Файл login.html не найден: {login_path}")
            return JSONResponse(
                status_code=500,
                content={"detail": f"Файл login.html не найден: {login_path}"}
            )
        logger.debug(f"✅ Возвращаем login.html: {login_path}")
        return FileResponse(str(login_path), headers={"Cache-Control": "no-store"})

    @app.get("/register")
    async def read_register():
        """Страница регистрации."""
        register_path = static_path / "register.html"
        if not register_path.exists():
            logger.error(f"❌ Файл register.html не найден: {register_path}")
            return JSONResponse(
                status_code=404,
                content={"detail": "Страница регистрации не найдена"}
            )
        return FileResponse(str(register_path), headers={"Cache-Control": "no-store"})

    @app.get("/forgot-password")
    async def read_forgot_password():
        """Страница восстановления пароля."""
        forgot_path = static_path / "forgot-password.html"
        if not forgot_path.exists():
            logger.error(f"❌ Файл forgot-password.html не найден: {forgot_path}")
            return JSONResponse(
                status_code=404,
                content={"detail": "Страница восстановления пароля не найдена"}
            )
        return FileResponse(str(forgot_path))

    @app.get("/reset-password")
    async def read_reset_password():
        """Страница сброса пароля по токену."""
        reset_path = static_path / "reset-password.html"
        if not reset_path.exists():
            logger.error(f"❌ Файл reset-password.html не найден: {reset_path}")
            return JSONResponse(
                status_code=404,
                content={"detail": "Страница сброса пароля не найдена"}
            )
        return FileResponse(str(reset_path))

    # Mount статических файлов ПОСЛЕ определения роутов
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

    @app.get("/app")
    async def read_app():
        """Страница выбора режима после аутентификации."""
        app_path = static_path / "app.html"
        if not app_path.exists():
            logger.error(f"❌ Файл app.html не найден: {app_path}")
            return JSONResponse(
                status_code=500,
                content={"detail": f"Файл app.html не найден: {app_path}"}
            )
        return FileResponse(str(app_path), headers={"Cache-Control": "no-store"})

    @app.get("/app/generate")
    async def read_app_generate():
        """Страница генератора README."""
        index_path = static_path / "index.html"
        if not index_path.exists():
            logger.error(f"❌ Файл index.html не найден: {index_path}")
            return JSONResponse(
                status_code=500,
                content={"detail": f"Файл index.html не найден: {index_path}"}
            )
        return FileResponse(str(index_path), headers={"Cache-Control": "no-store"})

    @app.get("/app/instruction")
    async def read_app_instruction():
        """Страница инструкции для методолога."""
        instruction_path = static_path / "instruction.html"
        if not instruction_path.exists():
            logger.error(f"❌ Файл instruction.html не найден: {instruction_path}")
            return JSONResponse(
                status_code=500,
                content={"detail": f"Файл instruction.html не найден: {instruction_path}"}
            )
        return FileResponse(str(instruction_path), headers={"Cache-Control": "no-store"})

    @app.get("/app/check")
    async def read_app_check():
        """Legacy alias for the full auditor page."""
        return RedirectResponse("/app/auditor", status_code=303)

    @app.get("/app/curriculum")
    async def read_app_curriculum():
        """Учебный план живёт в контуре справочника."""
        return RedirectResponse("/app/spravochnik/up", status_code=303)

    @app.get("/app/spravochnik")
    @app.get("/app/spravochnik/")
    async def read_app_spravochnik():
        """Главная точка входа в справочник."""
        return RedirectResponse("/app/spravochnik/intake", status_code=303)

    @app.get("/app/translate")
    async def read_app_translate():
        """Страница перевода README."""
        translator_path = static_path / "translator.html"
        if not translator_path.exists():
            logger.error(f"❌ Файл translator.html не найден: {translator_path}")
            return JSONResponse(
                status_code=500,
                content={"detail": f"Файл translator.html не найден: {translator_path}"},
            )
        return FileResponse(str(translator_path), headers={"Cache-Control": "no-store"})
else:
    logger.warning(f"⚠️ Директория static не найдена: {static_dir.absolute()}")

# Catalog UI (Phase 5, cutover complete): the entire Spravochnik viewer is served by
# native FastAPI routers — no WSGI mount / PrefixRewrite. The legacy wsgiref viewer stays
# runnable standalone (``python -m content_factory.catalog.viewer.app``) for parity checks.
if CATALOG_STATIC_DIR.exists():
    app.mount("/app/spravochnik/static", StaticFiles(directory=str(CATALOG_STATIC_DIR)), name="catalog-static")
app.include_router(catalog_pages.router)
app.include_router(catalog_admin_ui.router)
app.include_router(catalog_intake_ui.router)
app.include_router(catalog_reviews_ui.router)
app.include_router(catalog_up_ui.router)

# Роутеры
app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(auditor.page_router)
app.include_router(auditor.router, prefix="/api/v1", tags=["auditor"])
app.include_router(generation.router, prefix="/api/v1", tags=["generation"])
app.include_router(health.router, prefix="/api/v1", tags=["health"])
app.include_router(regeneration.router, prefix="/api/v1", tags=["regeneration"])
app.include_router(download.router, prefix="/api/v1", tags=["download"])
app.include_router(metrics.router, prefix="/api/v1", tags=["metrics"])
app.include_router(thematic_blocks.router, prefix="/api/v1", tags=["thematic-blocks"])
app.include_router(excel_parser.router, prefix="/api/v1", tags=["excel"])
app.include_router(admin.router, prefix="/api/v1/admin", tags=["admin"])
app.include_router(readme_check.router, prefix="/api/v1", tags=["readme-check"])
app.include_router(readme_improvement.router, prefix="/api/v1", tags=["readme-improvement"])
app.include_router(readme_translate.router, prefix="/api/v1", tags=["readme-translate"])
app.include_router(curriculum.router, prefix="/api/v1", tags=["curriculum"])

# Limiter уже настроен в generation.py через декоратор


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Глобальный обработчик исключений для возврата понятных ошибок."""
    logger.error(f"❌ Необработанное исключение: {exc}", exc_info=True)
    expose_details = os.getenv("EXPOSE_ERROR_DETAILS", "false").lower() in {"1", "true", "yes", "on"}
    content = {"detail": "Внутренняя ошибка сервера"}
    if expose_details:
        content.update({"error": str(exc), "type": type(exc).__name__})
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=content,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Обработчик ошибок валидации."""
    logger.warning(f"⚠️ Ошибка валидации запроса: {exc}")
    expose_details = os.getenv("EXPOSE_ERROR_DETAILS", "false").lower() in {"1", "true", "yes", "on"}
    content = {"detail": "Ошибка валидации запроса"}
    if expose_details:
        content["errors"] = exc.errors()
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=content,
    )


async def startup_event():
    """События при запуске приложения."""
    logger.info("🚀 FastAPI приложение запущено")
    logger.info(f"📝 Режим: {'development' if os.getenv('RELOAD', 'true').lower() == 'true' else 'production'}")
    logger.info(f"🌐 CORS origins: {os.getenv('CORS_ORIGINS', '*')}")

    # Убеждаемся, что таблицы БД созданы
    try:
        auto_create_tables = should_auto_create_tables()
        init_db(auto_create=auto_create_tables)
        if auto_create_tables:
            logger.info("✅ База данных инициализирована через SQLAlchemy metadata")
        else:
            logger.info("✅ База данных доступна; схема управляется Alembic")
        if os.getenv("WORKFLOW_RECOVERY_ON_STARTUP", "true").lower() in {"1", "true", "yes", "on"}:
            from content_factory.api.db.user_runs_db import reconcile_stale_active_user_runs
            from content_factory.api.services.generation_workflow_service import GenerationWorkflowService

            interrupted = await asyncio.to_thread(
                GenerationWorkflowService().mark_interrupted_active_workflows
            )
            stale_runs = await asyncio.to_thread(reconcile_stale_active_user_runs)
            if interrupted:
                logger.warning(
                    "♻️ Восстановление workflow: помечено interrupted запусков=%s",
                    len(interrupted),
                )
            if stale_runs:
                logger.warning(
                    "♻️ Dashboard cleanup: reconciled stale active runs=%s",
                    len(stale_runs),
                )
    except Exception as e:
        db_status = get_database_status()
        logger.error(
            "⚠️ Ошибка при инициализации БД: target=%s error=%s",
            db_status.get("target"),
            db_status.get("error") or str(e),
        )

    # Запускаем периодическую очистку старых логов
    cleanup_task = None

    async def periodic_log_cleanup():
        """Периодическая очистка старых логов из БД."""
        from content_factory.api.db.logging_db import cleanup_old_logs_async
        from content_factory.api.db.maintenance_db import cleanup_old_runtime_state_async

        # Интервал очистки: каждые 6 часов
        cleanup_interval = 6 * 60 * 60  # 6 часов в секундах
        # Количество дней для хранения логов (по умолчанию 1)
        days_to_keep = int(os.getenv("LOG_RETENTION_DAYS", "1"))
        workflow_days_to_keep = int(os.getenv("WORKFLOW_STATE_RETENTION_DAYS", "14"))
        paused_days_to_keep = int(os.getenv("PAUSED_SESSION_RETENTION_DAYS", "14"))
        maintenance_batch_size = int(os.getenv("DB_MAINTENANCE_BATCH_SIZE", "500"))

        try:
            while True:
                try:
                    await asyncio.sleep(cleanup_interval)
                    logger.info(f"🧹 Начинаем очистку старых логов (старше {days_to_keep} дней)...")
                    deleted_count = await cleanup_old_logs_async(days_to_keep=days_to_keep)
                    if deleted_count > 0:
                        logger.info(f"✅ Удалено {deleted_count} старых записей логов")
                    else:
                        logger.debug("ℹ️ Старых логов для удаления не найдено")
                    cleanup_counts = await cleanup_old_runtime_state_async(
                        workflow_days_to_keep=workflow_days_to_keep,
                        paused_days_to_keep=paused_days_to_keep,
                        batch_size=maintenance_batch_size,
                    )
                    cleaned_runtime = sum(cleanup_counts.values())
                    if cleaned_runtime > 0:
                        logger.info("✅ Runtime state cleanup: %s", cleanup_counts)
                except asyncio.CancelledError:
                    logger.info("🛑 Задача очистки логов отменена")
                    raise
                except Exception as e:
                    logger.error(f"❌ Ошибка при очистке старых логов: {e}", exc_info=True)
                    # При ошибке ждем 1 час перед следующей попыткой
                    try:
                        await asyncio.sleep(3600)
                    except asyncio.CancelledError:
                        logger.info("🛑 Задача очистки логов отменена")
                        raise
        except asyncio.CancelledError:
            logger.info("🛑 Задача очистки логов завершена")
            raise

    # Запускаем фоновую задачу очистки
    cleanup_task = asyncio.create_task(periodic_log_cleanup())
    logger.info("✅ Периодическая очистка логов запущена (каждые 6 часов)")

    # Сохраняем задачу для корректного завершения
    app.state.cleanup_task = cleanup_task


async def shutdown_event():
    """События при остановке приложения."""
    logger.info("🛑 Начало корректного завершения работы приложения...")

    # Отменяем фоновую задачу очистки логов
    try:
        cleanup_task = getattr(app.state, 'cleanup_task', None)
        if cleanup_task and not cleanup_task.done():
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass
            logger.info("✅ Фоновая задача очистки логов отменена")
    except Exception as e:
        logger.warning(f"⚠️ Ошибка при отмене задачи очистки: {e}")

    # Закрываем пул потоков для логирования
    try:
        from content_factory.api.db.logging_db import _executor
        _executor.shutdown(wait=False, cancel_futures=True)
        logger.info("✅ Пул потоков для логирования закрыт")
    except Exception as e:
        logger.warning(f"⚠️ Ошибка при закрытии пула потоков: {e}")

    logger.info("🛑 FastAPI приложение корректно остановлено")
