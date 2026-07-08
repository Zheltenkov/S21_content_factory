"""Integration proof that cookie-only auth works through the real FastAPI DI stack.

Complements the direct-call unit tests in test_auth_password_reset.py: here the
request goes through TestClient -> FastAPI dependency resolution, so it verifies
that ``get_current_user`` still gets its ``Request`` injected and reads the
HttpOnly cookie when no Authorization header is present (the cookie-only SPA path).
"""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from content_factory.api.db.models import Base, User, UserSession
from content_factory.api.db.session import get_db_session
from content_factory.api.dependencies import get_current_user
from content_factory.api.routers import auth


def _seeded_session_factory() -> tuple[sessionmaker, str]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    user = User(
        email="cookie-int@21-school.ru",
        username="cookie-int",
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
            session_token="int-session",
            token_hash=auth.hash_token("int-session"),
            is_active="true",
        )
    )
    db.commit()
    token = auth.create_access_token(
        {
            "sub": f"user_{user.id}",
            "username": user.username,
            "email": user.email,
            "role": user.role,
            "session_token": "int-session",
        }
    )
    db.close()
    return factory, token


def _client(factory: sessionmaker) -> TestClient:
    app = FastAPI()

    @app.get("/protected")
    async def protected(user: dict = Depends(get_current_user)) -> dict:  # type: ignore[type-arg]
        return {"id": user["id"]}

    def override_db():  # type: ignore[no-untyped-def]
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db_session] = override_db
    return TestClient(app)


def test_cookie_only_request_authenticates() -> None:
    factory, token = _seeded_session_factory()
    client = _client(factory)

    response = client.get("/protected", headers={"Cookie": f"content_gen_auth={token}"})

    assert response.status_code == 200
    assert response.json()["id"].startswith("user_")


def test_no_cookie_no_bearer_is_401() -> None:
    factory, _ = _seeded_session_factory()
    client = _client(factory)

    response = client.get("/protected")

    assert response.status_code == 401


def test_bearer_header_still_works() -> None:
    factory, token = _seeded_session_factory()
    client = _client(factory)

    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
