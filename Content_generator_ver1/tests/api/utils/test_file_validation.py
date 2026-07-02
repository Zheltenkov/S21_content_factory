"""Тесты валидации загружаемых файлов."""

from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException, UploadFile, status

from api.utils.file_validation import validate_file, validate_files


class TestFileValidation:
    """Тесты для валидации файлов."""

    def test_default_video_upload_limit_is_500_mb(self, monkeypatch):
        """Тест дефолтного лимита видео для UI/API контракта."""
        monkeypatch.delenv("MAX_VIDEO_SIZE_BYTES", raising=False)
        import importlib

        import api.utils.file_validation as fv_module
        importlib.reload(fv_module)

        assert fv_module.MAX_VIDEO_SIZE == 500 * 1024 * 1024

    def test_env_example_video_upload_limit_is_500_mb(self):
        """Тест документированного лимита видео в .env.example."""
        from pathlib import Path

        env_example = Path(__file__).resolve().parents[3] / ".env.example"
        content = env_example.read_text(encoding="utf-8")

        assert "MAX_VIDEO_SIZE_BYTES=524288000" in content
        assert "MAX_VIDEO_SIZE_BYTES=104857600" not in content

    def test_validate_file_valid(self):
        """Тест валидации валидного файла."""
        file = Mock(spec=UploadFile)
        file.filename = "test.txt"
        file.size = None
        # Не должно быть исключения
        validate_file(file)

    def test_validate_file_invalid_extension(self):
        """Тест валидации файла с недопустимым расширением."""
        from unittest.mock import Mock
        file = Mock(spec=UploadFile)
        file.filename = "test.exe"
        file.size = None
        with pytest.raises(HTTPException) as exc_info:
            validate_file(file)
        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "не разрешено" in exc_info.value.detail.lower()

    def test_validate_file_path_traversal(self):
        """Тест защиты от path traversal."""
        file = Mock(spec=UploadFile)
        file.filename = "../../../etc/passwd"
        file.size = None
        with pytest.raises(HTTPException) as exc_info:
            validate_file(file)
        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "недопустимое имя" in exc_info.value.detail.lower()

    def test_validate_file_forbidden_filename(self):
        """Тест защиты от запрещенных имен файлов."""
        file = Mock(spec=UploadFile)
        file.filename = "CON"
        file.size = None
        with pytest.raises(HTTPException) as exc_info:
            validate_file(file)
        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST

    def test_validate_file_too_large(self, monkeypatch):
        """Тест валидации слишком большого файла."""
        # Устанавливаем маленький лимит для теста
        monkeypatch.setenv("MAX_FILE_SIZE_BYTES", "10")
        import importlib

        import api.utils.file_validation as fv_module
        importlib.reload(fv_module)

        file = Mock(spec=UploadFile)
        file.filename = "test.txt"
        file.size = 100

        with pytest.raises(HTTPException) as exc_info:
            fv_module.validate_file(file)
        assert exc_info.value.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE

    @pytest.mark.asyncio
    async def test_validate_files_valid(self):
        """Тест валидации списка валидных файлов."""
        files = []
        for i, name in enumerate(["test1.txt", "test2.md"]):
            file = Mock(spec=UploadFile)
            file.filename = name
            file.size = None
            file.read = AsyncMock(return_value=b"content")
            file.seek = AsyncMock()
            files.append(file)

        # Не должно быть исключения
        await validate_files(files)

    @pytest.mark.asyncio
    async def test_validate_files_too_many(self, monkeypatch):
        """Тест валидации слишком большого количества файлов."""
        monkeypatch.setenv("MAX_FILES_COUNT", "2")
        import importlib

        import api.utils.file_validation as fv_module
        importlib.reload(fv_module)

        files = []
        for i in range(3):
            file = Mock(spec=UploadFile)
            file.filename = f"test{i}.txt"
            file.size = None
            file.read = AsyncMock(return_value=b"content")
            file.seek = AsyncMock()
            files.append(file)

        with pytest.raises(HTTPException) as exc_info:
            await fv_module.validate_files(files)
        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert "слишком много файлов" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_validate_files_total_size_exceeded(self, monkeypatch):
        """Тест валидации превышения общего размера файлов."""
        monkeypatch.setenv("MAX_TOTAL_FILES_SIZE_BYTES", "100")
        import importlib

        import api.utils.file_validation as fv_module
        importlib.reload(fv_module)

        files = []
        for i, size in enumerate([60, 60]):
            file = Mock(spec=UploadFile)
            file.filename = f"test{i+1}.txt"
            file.size = None
            file.read = AsyncMock(return_value=b"x" * size)
            file.seek = AsyncMock()
            files.append(file)

        with pytest.raises(HTTPException) as exc_info:
            await fv_module.validate_files(files)
        assert exc_info.value.status_code == status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
        assert "общий размер" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_validate_files_empty_list(self):
        """Тест валидации пустого списка файлов."""
        # Не должно быть исключения
        await validate_files([])

    def test_validate_file_allowed_extensions(self):
        """Тест проверки разрешенных расширений."""
        allowed = [".txt", ".md", ".py", ".json", ".yaml", ".pdf"]
        for ext in allowed:
            file = Mock(spec=UploadFile)
            file.filename = f"test{ext}"
            file.size = None
            # Не должно быть исключения
            validate_file(file)

