"""Тесты проверки прав доступа в download router."""

from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException, status

from api.routers.download import download_results
from api.utils.result_cache import get_result, store_result
from content_gen.orchestrator import OrchestratorResult


class TestDownloadAccessControl:
    """Тесты для проверки прав доступа при скачивании."""

    @pytest.fixture
    def mock_user(self):
        """Создает мок пользователя."""
        return {"id": "user_123", "email": "test@example.com"}

    @pytest.fixture
    def mock_other_user(self):
        """Создает мок другого пользователя."""
        return {"id": "user_456", "email": "other@example.com"}

    @pytest.fixture
    def mock_result(self):
        """Создает мок результата генерации."""
        result = Mock(spec=OrchestratorResult)
        result.report_json = {
            "markdown": "# Test README",
            "rubric": {"score": 100}
        }
        result.warnings = []
        result.assets = {}
        result.flow_trace = []
        return result

    @pytest.fixture
    def mock_db_result(self):
        """Создает мок результата из БД."""
        db_result = Mock()
        db_result.user_id = "user_123"
        db_result.markdown = "# Test README"
        return db_result

    @pytest.mark.asyncio
    async def test_download_own_result_from_cache(self, mock_user, mock_result):
        """Тест скачивания собственного результата из кэша."""
        request_id = "test_request_own"
        user_id = mock_user["id"]

        # Сохраняем результат в кэш
        store_result(request_id, mock_result, user_id=user_id)

        # Мокаем зависимости
        with patch("api.routers.download.get_result", return_value=get_result(request_id)):
            with patch("api.routers.download.get_generation_result", return_value=None):
                with patch("api.routers.download.get_report_by_request_id", return_value=mock_result.report_json):
                    # Не должно быть исключения
                    try:
                        await download_results(
                            request_id=request_id,
                            include_regenerated=False,
                            user=mock_user
                        )
                    except HTTPException as e:
                        # Может быть 404 если нет markdown, но не 403
                        assert e.status_code != status.HTTP_403_FORBIDDEN

    @pytest.mark.asyncio
    async def test_download_other_user_result_from_cache(self, mock_user, mock_other_user, mock_result):
        """Тест попытки скачать чужой результат из кэша."""
        request_id = "test_request_other"
        owner_id = mock_other_user["id"]

        # Сохраняем результат в кэш от имени другого пользователя
        store_result(request_id, mock_result, user_id=owner_id)

        # Мокаем зависимости
        with patch("api.routers.download.get_result", return_value=get_result(request_id)):
            with patch("api.routers.download.get_generation_result", return_value=None):
                # Попытка скачать от имени другого пользователя должна вызвать 403
                with pytest.raises(HTTPException) as exc_info:
                    await download_results(
                        request_id=request_id,
                        include_regenerated=False,
                        user=mock_user
                    )

                assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
                assert "нет доступа" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_download_other_user_result_from_db(self, mock_user, mock_other_user, mock_db_result):
        """Тест попытки скачать чужой результат из БД."""
        request_id = "test_request_db_other"
        mock_db_result.user_id = mock_other_user["id"]

        # Мокаем зависимости
        with patch("api.routers.download.get_result", return_value=None):
            with patch("api.routers.download.get_generation_result", return_value=mock_db_result):
                # Попытка скачать от имени другого пользователя должна вызвать 403
                with pytest.raises(HTTPException) as exc_info:
                    await download_results(
                        request_id=request_id,
                        include_regenerated=False,
                        user=mock_user
                    )

                assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
                assert "нет доступа" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_download_result_without_user_id(self, mock_user, mock_result):
        """Тест скачивания результата без user_id (старый формат)."""
        request_id = "test_request_no_user"

        # Сохраняем результат без user_id
        store_result(request_id, mock_result)  # Без user_id

        # Мокаем зависимости
        with patch("api.routers.download.get_result", return_value=get_result(request_id)):
            with patch("api.routers.download.get_generation_result", return_value=None):
                with patch("api.routers.download.get_report_by_request_id", return_value=mock_result.report_json):
                    # Не должно быть 403 (для обратной совместимости)
                    try:
                        await download_results(
                            request_id=request_id,
                            include_regenerated=False,
                            user=mock_user
                        )
                    except HTTPException as e:
                        # Может быть 404, но не 403
                        assert e.status_code != status.HTTP_403_FORBIDDEN

