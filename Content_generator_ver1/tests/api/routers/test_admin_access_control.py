"""Тесты проверки прав администратора в admin router."""

from unittest.mock import Mock

import pytest
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from api.db.models import User
from api.routers.admin import is_admin


class TestAdminAccessControl:
    """Тесты для проверки прав администратора."""

    @pytest.fixture
    def mock_admin_user(self):
        """Создает мок администратора."""
        return {"id": 1, "email": "admin@example.com", "role": "admin"}

    @pytest.fixture
    def mock_regular_user(self):
        """Создает мок обычного пользователя."""
        return {"id": 2, "email": "user@example.com", "role": "user"}

    @pytest.fixture
    def mock_db_session(self):
        """Создает мок сессии БД."""
        return Mock(spec=Session)

    def test_is_admin_with_admin_role(self, mock_admin_user, mock_db_session):
        """Тест проверки администратора с ролью admin."""
        # Создаем мок пользователя из БД
        db_user = Mock(spec=User)
        db_user.id = mock_admin_user["id"]
        db_user.role = "admin"
        db_user.is_active = True

        # Мокаем запрос к БД
        mock_query = Mock()
        mock_query.filter.return_value.first.return_value = db_user
        mock_db_session.query.return_value = mock_query

        # Не должно быть исключения
        result = is_admin(user=mock_admin_user, db=mock_db_session)
        assert result == mock_admin_user

    def test_is_admin_accepts_jwt_subject_format(self, mock_db_session):
        """JWT subject user_<id> должен сопоставляться с числовым users.id."""
        jwt_user = {"id": "user_1", "email": "admin@example.com", "role": "admin"}
        db_user = Mock(spec=User)
        db_user.id = 1
        db_user.role = "admin"
        db_user.is_active = True

        mock_query = Mock()
        mock_query.filter.return_value.first.return_value = db_user
        mock_db_session.query.return_value = mock_query

        result = is_admin(user=jwt_user, db=mock_db_session)

        assert result == jwt_user

    def test_is_admin_with_user_role(self, mock_regular_user, mock_db_session):
        """Тест проверки обычного пользователя (должен быть отклонен)."""
        # Создаем мок пользователя из БД с ролью user
        db_user = Mock(spec=User)
        db_user.id = mock_regular_user["id"]
        db_user.role = "user"
        db_user.is_active = True

        # Мокаем запрос к БД
        mock_query = Mock()
        mock_query.filter.return_value.first.return_value = db_user
        mock_db_session.query.return_value = mock_query

        # Должно быть исключение 403
        with pytest.raises(HTTPException) as exc_info:
            is_admin(user=mock_regular_user, db=mock_db_session)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert "администратора" in exc_info.value.detail.lower()

    def test_is_admin_user_not_found(self, mock_admin_user, mock_db_session):
        """Тест проверки когда пользователь не найден в БД."""
        # Мокаем запрос к БД (возвращает None)
        mock_query = Mock()
        mock_query.filter.return_value.first.return_value = None
        mock_db_session.query.return_value = mock_query

        # Должно быть исключение 403
        with pytest.raises(HTTPException) as exc_info:
            is_admin(user=mock_admin_user, db=mock_db_session)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert "не найден" in exc_info.value.detail.lower()

    def test_is_admin_inactive_user(self, mock_admin_user, mock_db_session):
        """Тест проверки неактивного администратора."""
        # Создаем мок неактивного пользователя
        db_user = Mock(spec=User)
        db_user.id = mock_admin_user["id"]
        db_user.role = "admin"
        db_user.is_active = False

        # Мокаем запрос к БД
        mock_query = Mock()
        mock_query.filter.return_value.first.return_value = db_user
        mock_db_session.query.return_value = mock_query

        # Должно быть исключение 403
        with pytest.raises(HTTPException) as exc_info:
            is_admin(user=mock_admin_user, db=mock_db_session)

        assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN
        assert "деактивирован" in exc_info.value.detail.lower()

    def test_is_admin_no_user_id(self, mock_db_session):
        """Тест проверки когда user_id отсутствует."""
        user_without_id = {"email": "test@example.com"}

        # Должно быть исключение 401
        with pytest.raises(HTTPException) as exc_info:
            is_admin(user=user_without_id, db=mock_db_session)

        assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert "не аутентифицирован" in exc_info.value.detail.lower()

