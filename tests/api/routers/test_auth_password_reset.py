import re
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from content_factory.api.db.models import Base, PasswordResetToken, User, UserSession
from content_factory.api.dependencies import get_current_user
from content_factory.api.routers import auth


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


def _route_handler(handler):
    return getattr(handler, "__wrapped__", handler)


def _extract_reset_token(html_body: str) -> str:
    match = re.search(r"/reset-password\?token=([^\"'\s<]+)", html_body)
    assert match, html_body
    return match.group(1)


@pytest.mark.asyncio
async def test_password_reset_flow_hashes_token_and_invalidates_sessions(monkeypatch) -> None:
    session_factory = _session_factory()
    db = session_factory()
    sent_email: dict[str, str] = {}

    async def fake_send_email_async(*, to_email: str, subject: str, html_body: str, text_body=None) -> bool:
        sent_email.update({"to_email": to_email, "subject": subject, "html_body": html_body})
        return True

    monkeypatch.setattr(auth, "ALLOWED_EMAIL_DOMAIN", "21-school.ru")
    monkeypatch.setattr(auth, "send_email_async", fake_send_email_async)

    user = User(
        email="alice@21-school.ru",
        username="alice",
        hashed_password=User.hash_password("old-password-123"),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    active_session = UserSession(
        user_id=f"user_{user.id}",
        user_id_fk=user.id,
        username=user.username,
        session_token="session-token",
        token_hash=auth.hash_token("session-token"),
        is_active="true",
    )
    db.add(active_session)
    db.commit()

    forgot = _route_handler(auth.forgot_password)
    reset = _route_handler(auth.reset_password)
    response = await forgot(
        auth.ForgotPasswordRequest(email="alice@21-school.ru"),
        request=SimpleNamespace(base_url="http://testserver/"),
        db=db,
    )

    assert response["message"] == "Если email существует, на него отправлена инструкция"
    raw_token = _extract_reset_token(sent_email["html_body"])
    reset_record = db.query(PasswordResetToken).filter_by(user_id=user.id).one()
    assert reset_record.token == auth.hash_token(raw_token)
    assert reset_record.token != raw_token

    reset_response = await reset(
        auth.ResetPasswordRequest(token=raw_token, new_password="new-password-456"),
        request=SimpleNamespace(base_url="http://testserver/"),
        db=db,
    )

    assert reset_response["message"] == "Пароль успешно изменен"
    db.refresh(user)
    db.refresh(reset_record)
    db.refresh(active_session)
    assert user.verify_password("new-password-456")
    assert not user.verify_password("old-password-123")
    assert reset_record.used is True
    assert active_session.is_active == "false"
    assert active_session.ended_at is not None

    with pytest.raises(HTTPException) as exc_info:
        await reset(
            auth.ResetPasswordRequest(token=raw_token, new_password="another-password-789"),
            request=SimpleNamespace(base_url="http://testserver/"),
            db=db,
        )
    assert exc_info.value.status_code == 400

    db.close()


@pytest.mark.asyncio
async def test_forgot_password_revokes_previous_unused_tokens(monkeypatch) -> None:
    session_factory = _session_factory()
    db = session_factory()
    sent_emails: list[str] = []

    async def fake_send_email_async(*, to_email: str, subject: str, html_body: str, text_body=None) -> bool:
        sent_emails.append(html_body)
        return True

    monkeypatch.setattr(auth, "ALLOWED_EMAIL_DOMAIN", "21-school.ru")
    monkeypatch.setattr(auth, "send_email_async", fake_send_email_async)

    user = User(
        email="bob@21-school.ru",
        username="bob",
        hashed_password=User.hash_password("old-password-123"),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    forgot = _route_handler(auth.forgot_password)
    request = SimpleNamespace(base_url="http://testserver/")
    await forgot(auth.ForgotPasswordRequest(email="bob@21-school.ru"), request=request, db=db)
    first_raw_token = _extract_reset_token(sent_emails[-1])
    await forgot(auth.ForgotPasswordRequest(email="bob@21-school.ru"), request=request, db=db)
    second_raw_token = _extract_reset_token(sent_emails[-1])

    first = db.query(PasswordResetToken).filter_by(token=auth.hash_token(first_raw_token)).one()
    second = db.query(PasswordResetToken).filter_by(token=auth.hash_token(second_raw_token)).one()
    assert first.used is True
    assert second.used is False

    db.close()


@pytest.mark.asyncio
async def test_reset_password_rejects_expired_token() -> None:
    session_factory = _session_factory()
    db = session_factory()
    user = User(
        email="carol@21-school.ru",
        username="carol",
        hashed_password=User.hash_password("old-password-123"),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    raw_token = "expired-token"
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token=auth.hash_token(raw_token),
            expires_at=datetime.utcnow() - timedelta(minutes=1),
            used=False,
        )
    )
    db.commit()

    reset = _route_handler(auth.reset_password)
    with pytest.raises(HTTPException) as exc_info:
        await reset(
            auth.ResetPasswordRequest(token=raw_token, new_password="new-password-456"),
            request=SimpleNamespace(base_url="http://testserver/"),
            db=db,
        )
    assert exc_info.value.status_code == 400

    db.close()


def test_reset_password_requires_server_side_minimum_length() -> None:
    with pytest.raises(ValidationError):
        auth.ResetPasswordRequest(token="token", new_password="short")


@pytest.mark.asyncio
async def test_login_unknown_user_returns_registration_hint(monkeypatch) -> None:
    session_factory = _session_factory()
    db = session_factory()
    monkeypatch.setattr(auth, "ALLOWED_EMAIL_DOMAIN", "21-school.ru")

    login = _route_handler(auth.login)
    with pytest.raises(HTTPException) as exc_info:
        await login(
            auth.LoginRequest(email="unknown@21-school.ru", password="password-123"),
            response=Response(),
            http_request=SimpleNamespace(client=None, headers={}),
            db=db,
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == auth.UNREGISTERED_LOGIN_MESSAGE
    db.close()


@pytest.mark.asyncio
async def test_login_rejects_non_school_domain_with_registration_hint(monkeypatch) -> None:
    session_factory = _session_factory()
    db = session_factory()
    monkeypatch.setattr(auth, "ALLOWED_EMAIL_DOMAIN", "21-school.ru")

    login = _route_handler(auth.login)
    with pytest.raises(HTTPException) as exc_info:
        await login(
            auth.LoginRequest(email="unknown@example.com", password="password-123"),
            response=Response(),
            http_request=SimpleNamespace(client=None, headers={}),
            db=db,
        )

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == auth.UNREGISTERED_LOGIN_MESSAGE
    db.close()


@pytest.mark.asyncio
async def test_get_current_user_rejects_revoked_session() -> None:
    session_factory = _session_factory()
    db = session_factory()
    user = User(
        email="revoked@21-school.ru",
        username="revoked",
        hashed_password=User.hash_password("password-123"),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(
        UserSession(
            user_id=f"user_{user.id}",
            user_id_fk=user.id,
            username=user.username,
            session_token="revoked-session",
            token_hash=auth.hash_token("revoked-session"),
            is_active="false",
        )
    )
    db.commit()
    token = auth.create_access_token(
        {
            "sub": f"user_{user.id}",
            "username": user.username,
            "email": user.email,
            "role": user.role,
            "session_token": "revoked-session",
        }
    )

    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=credentials, db=db)
    assert exc_info.value.status_code == 401

    db.close()


@pytest.mark.asyncio
async def test_current_user_profile_returns_safe_session_payload() -> None:
    result = await auth.current_user_profile(
        user={
            "id": "user_1",
            "username": "methodologist",
            "email": "methodologist@21-school.ru",
            "role": "user",
            "session_token": "secret-session-token",
        }
    )

    assert result == {
        "id": "user_1",
        "username": "methodologist",
        "email": "methodologist@21-school.ru",
        "role": "user",
    }
    assert "session_token" not in result


@pytest.mark.asyncio
async def test_get_sessions_requires_owner_and_redacts_tokens() -> None:
    session_factory = _session_factory()
    db = session_factory()
    user = User(
        email="sessions@21-school.ru",
        username="sessions",
        hashed_password=User.hash_password("password-123"),
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(
        UserSession(
            user_id=f"user_{user.id}",
            user_id_fk=user.id,
            username=user.username,
            session_token="secret-session",
            token_hash=auth.hash_token("secret-session"),
            is_active="true",
        )
    )
    db.commit()

    result = await auth.get_sessions(user={"id": f"user_{user.id}", "role": "user"}, db=db)
    assert result["total"] == 1
    assert "session_token" not in result["sessions"][0]
    assert "token_hash" not in result["sessions"][0]

    with pytest.raises(HTTPException) as exc_info:
        await auth.get_sessions(user_id="user_999", user={"id": f"user_{user.id}", "role": "user"}, db=db)
    assert exc_info.value.status_code == 403

    db.close()
