"""Health check endpoint."""

import os
import time
from typing import Any

import psutil
from fastapi import APIRouter

from content_factory.api.db.session import check_database_connection, get_database_status
from content_factory.api.utils.logger import get_logger
from content_factory.platform.llm.model_registry import get_llm_provider_summary

router = APIRouter()
logger = get_logger("health")


@router.get("/health")
async def health_check() -> dict[str, Any]:
    """
    Расширенная проверка здоровья сервиса.
    
    Проверяет:
    - Подключение к БД
    - Доступность внешних LLM provider-ов
    - Использование ресурсов (память, CPU)
    
    Args:
        db: Сессия БД
        
    Returns:
        Статус сервиса и его компонентов
    """
    status = "healthy"
    checks = {}

    # Проверка подключения к БД
    try:
        check_database_connection()
        checks["database"] = {
            "status": "ok",
            "message": "Connected",
            "target": get_database_status().get("target"),
        }
    except Exception as e:
        status = "unhealthy"
        db_status = get_database_status()
        checks["database"] = {
            "status": "error",
            "message": db_status.get("error") or str(e),
            "target": db_status.get("target"),
        }
        logger.error("Database health check failed: %s", checks["database"]["message"])

    # Проверка доступности выбранного LLM provider.
    try:
        llm_summary = get_llm_provider_summary()
        llm_available = bool(llm_summary["available"])
        checks["llm"] = {
            "status": "ok" if llm_available else "warning",
            "available": llm_available,
            "provider": llm_summary["provider"],
            "model": llm_summary["model"],
            "base_url": llm_summary["base_url"],
            "message": (
                f"LLM provider configured via {llm_summary['credential_env']}"
                if llm_available
                else f"LLM provider not configured: set {llm_summary['credential_env']}"
            ),
        }
    except Exception as e:
        llm_available = False
        checks["llm"] = {
            "status": "error",
            "available": False,
            "provider": os.getenv("LLM_PROVIDER", "polza"),
            "message": str(e),
        }

    if not llm_available:
        status = "degraded"

    # Контекст генерации
    checks["generation_context"] = {
        "status": "ok",
        "mode": "curriculum_only",
        "message": "Production generation uses curriculum_context and explicit reference hints",
    }

    # Проверка Redis (если используется)
    redis_url = os.getenv("REDIS_URL")
    if redis_url and redis_url != "":
        try:
            import redis
            redis_client = redis.from_url(redis_url, decode_responses=True)
            redis_client.ping()
            checks["redis"] = {
                "status": "ok",
                "available": True,
                "message": "Redis connected"
            }
        except ImportError:
            checks["redis"] = {
                "status": "warning",
                "available": False,
                "message": "Redis URL configured but redis package not installed"
            }
        except Exception as e:
            checks["redis"] = {
                "status": "error",
                "available": False,
                "message": f"Redis connection failed: {e}"
            }
            if status == "healthy":
                status = "degraded"
    else:
        checks["redis"] = {
            "status": "ok",
            "available": False,
            "message": "Redis not configured (optional)"
        }

    # Метрики использования ресурсов
    try:
        process = psutil.Process()
        memory_info = process.memory_info()
        cpu_percent = process.cpu_percent(interval=0.1)

        checks["resources"] = {
            "status": "ok",
            "memory_mb": round(memory_info.rss / 1024 / 1024, 2),
            "cpu_percent": round(cpu_percent, 2),
            "memory_percent": round(process.memory_percent(), 2)
        }
    except Exception as e:
        checks["resources"] = {
            "status": "warning",
            "message": f"Could not get resource metrics: {e}"
        }

    return {
        "status": status,
        "version": "1.0.0",
        "timestamp": time.time(),
        "checks": checks
    }
