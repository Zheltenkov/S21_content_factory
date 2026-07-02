"""Тесты кэша результатов генерации."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest

from api.utils.result_cache import (
    clear_expired,
    clear_result,
    get_generation_error,
    get_generation_methodology,
    get_generation_status,
    get_result,
    set_generation_methodology,
    set_generation_status,
    store_generation_error,
    store_result,
)
from content_gen.orchestrator import OrchestratorResult


class TestResultCache:
    """Тесты для кэша результатов."""

    @pytest.fixture
    def mock_result(self):
        """Создает мок результата генерации."""
        result = Mock(spec=OrchestratorResult)
        result.report_json = {
            "markdown": "# Test",
            "rubric": {"score": 100},
            "methodology_gate": {
                "summary": {"total_decisions": 1, "latest_action": "continue"},
                "decisions": [{"stage": "context", "action": "continue"}],
            },
        }
        result.warnings = []
        result.assets = {}
        result.flow_trace = []
        return result

    def test_store_and_get_result(self, mock_result):
        """Тест сохранения и получения результата."""
        request_id = "test_request_1"
        user_id = "user_123"

        store_result(request_id, mock_result, user_id=user_id)
        cached = get_result(request_id)

        assert cached is not None
        assert cached["user_id"] == user_id
        assert cached["markdown"] == "# Test"
        assert cached["methodology"]["summary"]["total_decisions"] == 1
        assert get_generation_methodology(request_id)["summary"]["latest_action"] == "continue"
        assert get_generation_status(request_id) == "completed"

    def test_get_result_nonexistent(self):
        """Тест получения несуществующего результата."""
        cached = get_result("nonexistent_request")
        assert cached is None

    def test_clear_result(self, mock_result):
        """Тест очистки результата."""
        request_id = "test_request_2"
        store_result(request_id, mock_result, user_id="user_123")

        assert get_result(request_id) is not None
        clear_result(request_id)
        assert get_result(request_id) is None

    def test_lru_eviction(self, mock_result, monkeypatch):
        """Тест LRU eviction при превышении лимита."""
        # Устанавливаем маленький лимит для теста
        monkeypatch.setenv("MAX_RESULT_CACHE_SIZE", "3")
        import importlib

        import api.utils.result_cache as cache_module
        importlib.reload(cache_module)

        # Очищаем кэш перед тестом
        from api.utils.result_cache import _generation_errors, _generation_methodology, _generation_status, _result_cache
        _result_cache.clear()
        _generation_status.clear()
        _generation_errors.clear()
        _generation_methodology.clear()

        # Сохраняем 4 результата (лимит = 3)
        for i in range(4):
            cache_module.store_result(f"test_{i}", mock_result, user_id=f"user_{i}")

        # Первый должен быть удален (LRU)
        assert cache_module.get_result("test_0") is None
        assert cache_module.get_result("test_1") is not None
        assert cache_module.get_result("test_2") is not None
        assert cache_module.get_result("test_3") is not None

    def test_lru_update_on_get(self, mock_result, monkeypatch):
        """Тест обновления LRU при получении результата."""
        monkeypatch.setenv("MAX_RESULT_CACHE_SIZE", "3")
        import importlib

        import api.utils.result_cache as cache_module
        importlib.reload(cache_module)

        # Очищаем кэш
        from api.utils.result_cache import _generation_errors, _generation_methodology, _generation_status, _result_cache
        _result_cache.clear()
        _generation_status.clear()
        _generation_errors.clear()
        _generation_methodology.clear()

        # Сохраняем 3 результата
        for i in range(3):
            cache_module.store_result(f"test_{i}", mock_result, user_id=f"user_{i}")

        # Получаем первый (обновляет его позицию в LRU)
        cache_module.get_result("test_0")

        # Добавляем еще один (должен удалить test_1, а не test_0)
        cache_module.store_result("test_3", mock_result, user_id="user_3")

        assert cache_module.get_result("test_0") is not None  # Должен остаться
        assert cache_module.get_result("test_1") is None  # Должен быть удален
        assert cache_module.get_result("test_2") is not None
        assert cache_module.get_result("test_3") is not None

    def test_generation_status(self):
        """Тест управления статусами генерации."""
        request_id = "test_status"

        set_generation_status(request_id, "pending")
        assert get_generation_status(request_id) == "pending"

        set_generation_status(request_id, "in_progress")
        assert get_generation_status(request_id) == "in_progress"

    def test_generation_error(self):
        """Тест сохранения и получения ошибок генерации."""
        request_id = "test_error"
        error_message = "Test error message"

        store_generation_error(request_id, error_message)
        assert get_generation_error(request_id) == error_message

    def test_generation_methodology_snapshot(self):
        """Тест live-снимка методологического gate."""
        request_id = "test_methodology"
        payload = {
            "summary": {"total_decisions": 2, "latest_action": "warn"},
            "decisions": [{"stage": "practice", "action": "warn"}],
        }

        set_generation_methodology(request_id, payload)

        assert get_generation_methodology(request_id) == payload

    def test_clear_expired(self, mock_result, monkeypatch):
        """Тест очистки истекших записей."""
        request_id = "test_expired"
        user_id = "user_123"

        store_result(request_id, mock_result, user_id=user_id)

        # Симулируем истечение TTL (устанавливаем старую дату)
        from api.utils.result_cache import _result_cache
        if request_id in _result_cache:
            cached = _result_cache[request_id]
            # Устанавливаем старую дату создания
            cached["created_at"] = (datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=25)).isoformat()

        # Очищаем истекшие
        clear_expired()

        # Результат должен быть удален
        assert get_result(request_id) is None

    def test_store_result_with_user_id(self, mock_result):
        """Тест сохранения результата с user_id."""
        request_id = "test_user_id"
        user_id = "user_456"

        store_result(request_id, mock_result, user_id=user_id)
        cached = get_result(request_id)

        assert cached is not None
        assert cached["user_id"] == user_id

    def test_store_result_without_user_id(self, mock_result):
        """Тест сохранения результата без user_id."""
        request_id = "test_no_user_id"

        store_result(request_id, mock_result)
        cached = get_result(request_id)

        assert cached is not None
        assert cached.get("user_id") is None
