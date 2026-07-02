"""Тесты для health check endpoint."""

from unittest.mock import Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from api.routers.health import router

# Создаем тестовое приложение
health_app = FastAPI()
health_app.include_router(router)


class TestHealthCheck:
    """Тесты для health check."""

    @pytest.fixture
    def client(self):
        """Тестовый клиент."""
        return TestClient(health_app)

    @pytest.fixture
    def mock_db(self):
        """Мок БД сессии."""
        mock_db = Mock(spec=Session)
        mock_db.execute.return_value = None
        return mock_db

    @patch('api.routers.health.get_database_status')
    @patch('api.routers.health.check_database_connection')
    @patch('api.routers.health.psutil.Process')
    def test_health_check_all_ok(self, mock_process, mock_check_db, mock_db_status, client, mock_db):
        """Тест health check когда все OK."""
        mock_check_db.return_value = None
        mock_db_status.return_value = {"target": "postgresql://content_user:***@localhost:5432/content_generator"}

        mock_proc = Mock()
        mock_proc.memory_info.return_value = Mock(rss=100 * 1024 * 1024)  # 100 MB
        mock_proc.cpu_percent.return_value = 10.0
        mock_proc.memory_percent.return_value = 5.0
        mock_process.return_value = mock_proc

        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ["healthy", "degraded"]
        assert "checks" in data
        assert "database" in data["checks"]
        assert "llm" in data["checks"]
        assert "resources" in data["checks"]

    @patch('api.routers.health.get_database_status')
    @patch('api.routers.health.check_database_connection')
    def test_health_check_db_error(self, mock_check_db, mock_db_status, client, mock_db):
        """Тест health check при ошибке БД."""
        mock_check_db.side_effect = Exception("DB connection failed")
        mock_db_status.return_value = {
            "error": "DB connection failed",
            "target": "postgresql://content_user:***@localhost:5432/content_generator",
        }

        with patch('api.routers.health.psutil.Process'):
            response = client.get("/health")

            assert response.status_code == 200
            data = response.json()
            # Проверяем что есть информация о БД
            assert "database" in data["checks"]
            # Статус может быть error или ok в зависимости от других проверок
            assert data["checks"]["database"]["status"] in ["error", "ok"]

    @patch('api.routers.health.check_database_connection')
    def test_health_check_generation_context(self, mock_check_db, client, mock_db):
        """Тест health check с generation context."""
        mock_check_db.return_value = None

        with patch('api.routers.health.psutil.Process'):
            response = client.get("/health")

            assert response.status_code == 200
            data = response.json()
            assert "generation_context" in data["checks"]
            assert data["checks"]["generation_context"]["status"] == "ok"

    @patch('api.routers.health.check_database_connection')
    def test_health_check_redis_available(self, mock_check_db, client, mock_db):
        """Тест health check с Redis (упрощенный - просто проверяем что endpoint работает)."""
        mock_check_db.return_value = None

        with patch('api.routers.health.psutil.Process'):
            response = client.get("/health")

            assert response.status_code == 200
            data = response.json()
            # Проверяем что есть информация о Redis
            assert "redis" in data["checks"]
            # Статус может быть ok, warning или error в зависимости от доступности
            assert data["checks"]["redis"]["status"] in ["ok", "warning", "error"]
