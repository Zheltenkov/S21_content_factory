import pytest
from fastapi import HTTPException

from api.db import session


def test_get_db_session_returns_503_when_startup_marked_database_unavailable() -> None:
    session.set_database_status(False, "password authentication failed")
    try:
        provider = session.get_db_session()
        with pytest.raises(HTTPException) as exc_info:
            next(provider)

        assert exc_info.value.status_code == 503
        assert exc_info.value.detail["type"] == "DatabaseUnavailable"
        assert "password authentication failed" in exc_info.value.detail["error"]
        assert ":***@" in exc_info.value.detail["target"]
    finally:
        session.set_database_status(None)


def test_get_db_session_does_not_mark_database_down_for_http_errors() -> None:
    session.set_database_status(True)
    provider = session.get_db_session()
    try:
        next(provider)
        with pytest.raises(HTTPException):
            provider.throw(HTTPException(status_code=401, detail="auth failed"))

        assert session.is_database_available() is True
    finally:
        session.set_database_status(None)


def test_database_status_redacts_password() -> None:
    status_payload = session.get_database_status()

    assert status_payload["target"]
    assert ":***@" in status_payload["target"]
