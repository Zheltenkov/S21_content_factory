"""Endpoints для аутентификации и управления сессиями."""

import hashlib
import os
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr, Field
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import or_
from sqlalchemy.orm import Session

from content_factory.api.db.models import PasswordResetToken, User, UserSession
from content_factory.api.db.session import get_db_session
from content_factory.api.dependencies import get_current_user
from content_factory.api.integrations.auth_cookie import clear_auth_cookie, request_token, set_auth_cookie
from content_factory.api.utils.email_service import get_password_reset_email_html, get_welcome_email_html, send_email_async
from content_factory.api.utils.logger import get_logger


def _parse_allowed_email_domains() -> tuple[str, ...]:
    """Return normalized allowed email domains from env, preserving legacy config."""
    raw_domains = os.getenv("ALLOWED_EMAIL_DOMAINS") or os.getenv("ALLOWED_EMAIL_DOMAIN", "21-school.ru")
    domains = tuple(
        domain.strip().lower().removeprefix("@")
        for domain in raw_domains.replace(";", ",").split(",")
        if domain.strip()
    )
    return domains or ("21-school.ru",)


ALLOWED_EMAIL_DOMAINS = _parse_allowed_email_domains()
ALLOWED_EMAIL_DOMAIN = ALLOWED_EMAIL_DOMAINS[0]
UNREGISTERED_LOGIN_MESSAGE = "Вы не зарегестрированы используйте домен школы 21"


def _allowed_domain_hint() -> str:
    """Build a human-readable list of allowed domains for auth errors."""
    return " или ".join(f"@{domain}" for domain in ALLOWED_EMAIL_DOMAINS)


def _is_allowed_email_domain(email: str) -> bool:
    """Check that normalized email belongs to one of the configured domains."""
    return any(email.endswith(f"@{domain}") for domain in ALLOWED_EMAIL_DOMAINS)


def _ensure_allowed_domain(email: str) -> str:
    """
    Проверяет, что email относится к разрешенному домену.
    Бросает HTTPException, если домен не совпадает.
    """
    normalized = (email or "").strip().lower()
    if not _is_allowed_email_domain(normalized):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Регистрация и восстановление пароля доступны только для адресов {_allowed_domain_hint()}",
        )
    return normalized


router = APIRouter()
logger = get_logger("auth")

# Rate limiter
limiter = Limiter(key_func=get_remote_address)

# JWT настройки
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_HOURS = int(os.getenv("JWT_EXPIRATION_HOURS", "24"))
PASSWORD_MIN_LENGTH = int(os.getenv("PASSWORD_MIN_LENGTH", "8"))

# Максимальное количество активных сессий на пользователя
MAX_ACTIVE_SESSIONS_PER_USER = int(os.getenv("MAX_ACTIVE_SESSIONS_PER_USER", "5"))

# Таймаут неактивных сессий (в часах)
SESSION_INACTIVITY_TIMEOUT_HOURS = int(os.getenv("SESSION_INACTIVITY_TIMEOUT_HOURS", "24"))


def hash_token(token: str) -> str:
    """
    Хеширует токен для безопасного хранения в БД.

    Args:
        token: Токен для хеширования

    Returns:
        SHA-256 хеш токена
    """
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


class LoginRequest(BaseModel):
    """Запрос на вход."""
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    """Ответ с токеном и информацией о сессии."""
    access_token: str
    token_type: str = "bearer"
    user_id: str
    username: str
    session_id: int


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """Создает JWT токен."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt


def _session_public_payload(session: UserSession) -> dict[str, Any]:
    """Возвращает сессию без bearer/session token material."""
    return {
        "id": session.id,
        "user_id": session.user_id,
        "username": session.username,
        "started_at": session.started_at.isoformat() if session.started_at else None,
        "last_activity": session.last_activity.isoformat() if session.last_activity else None,
        "ip_address": session.ip_address,
        "user_agent": session.user_agent,
        "is_active": session.is_active == "true",
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
    }


@router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    response: Response,
    http_request: Request,
    db: Session = Depends(get_db_session)
):
    """
    Аутентифицирует пользователя по email и паролю.

    Args:
        request: Данные для входа (email и пароль)
        http_request: HTTP запрос (для получения IP и User-Agent)
        db: Сессия БД

    Returns:
        JWT токен и информация о сессии

    Raises:
        HTTPException: Если пароль неверный или произошла ошибка
    """
    # Нормализуем email один раз, чтобы поиск и проверка домена были предсказуемыми.
    normalized_email = request.email.lower()

    if not _is_allowed_email_domain(normalized_email):
        logger.warning("⚠️ Попытка входа с неразрешенным доменом: %s", request.email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=UNREGISTERED_LOGIN_MESSAGE,
        )

    # Ищем пользователя по email.
    user = db.query(User).filter(User.email == normalized_email).first()

    if not user:
        logger.warning(f"⚠️ Попытка входа с несуществующим email: {request.email}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=UNREGISTERED_LOGIN_MESSAGE,
        )

    # Проверяем блокировку
    if user.locked_until and user.locked_until > datetime.utcnow():
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"Аккаунт заблокирован до {user.locked_until.strftime('%Y-%m-%d %H:%M:%S')}"
        )

    # Проверяем активность
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Аккаунт деактивирован"
        )

    # Проверяем пароль
    if not user.verify_password(request.password):
        user.failed_login_attempts += 1

        # Блокировка после 5 неудачных попыток на 30 минут
        MAX_FAILED_ATTEMPTS = int(os.getenv("MAX_FAILED_LOGIN_ATTEMPTS", "5"))
        LOCKOUT_MINUTES = int(os.getenv("LOGIN_LOCKOUT_MINUTES", "30"))

        if user.failed_login_attempts >= MAX_FAILED_ATTEMPTS:
            user.locked_until = datetime.utcnow() + timedelta(minutes=LOCKOUT_MINUTES)
            logger.warning(f"🔒 Аккаунт заблокирован: {user.email} до {user.locked_until}")

        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный email или пароль"
        )

    # Успешный вход - сбрасываем счетчик
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login = datetime.utcnow()

    # Если пароль был захеширован старым алгоритмом (bcrypt), перехешируем в Argon2
    if user.needs_rehash():
        user.hashed_password = User.hash_password(request.password)
        logger.info(f"🔄 Пароль перехеширован в Argon2 для пользователя: {user.email}")

    db.commit()

    user_id = f"user_{user.id}"

    # Создаем токен сессии
    session_token = str(uuid.uuid4())
    token_hash = hash_token(session_token)

    # Проверяем количество активных сессий пользователя
    try:
        active_sessions = db.query(UserSession).filter(
            UserSession.user_id == user_id,
            UserSession.is_active == "true"
        ).count()
    except Exception as e:
        logger.error(f"❌ Ошибка при проверке активных сессий: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Схема БД не готова для пользовательских сессий. Выполните Alembic migrations.",
        ) from e

    # Если превышен лимит, закрываем самые старые неактивные сессии
    if active_sessions >= MAX_ACTIVE_SESSIONS_PER_USER:
        # Закрываем сессии, которые неактивны дольше таймаута
        timeout_threshold = datetime.utcnow() - timedelta(hours=SESSION_INACTIVITY_TIMEOUT_HOURS)
        old_sessions = db.query(UserSession).filter(
            UserSession.user_id == user_id,
            UserSession.is_active == "true",
            UserSession.last_activity < timeout_threshold
        ).all()

        for old_session in old_sessions:
            old_session.is_active = "false"
            old_session.ended_at = datetime.utcnow()

        db.commit()

        # Если все еще превышен лимит, закрываем самую старую сессию
        if active_sessions >= MAX_ACTIVE_SESSIONS_PER_USER:
            oldest_session = db.query(UserSession).filter(
                UserSession.user_id == user_id,
                UserSession.is_active == "true"
            ).order_by(UserSession.last_activity.asc()).first()

            if oldest_session:
                oldest_session.is_active = "false"
                oldest_session.ended_at = datetime.utcnow()
                db.commit()
                logger.info(f"Закрыта старая сессия {oldest_session.id} для пользователя {user_id}")

    # Создаем JWT токен
    access_token = create_access_token(
        data={
            "sub": user_id,
            "username": user.username,
            "email": user.email,
            "role": user.role,
            "session_token": session_token
        }
    )

    # Получаем IP адрес и User-Agent
    ip_address = http_request.client.host if http_request.client else None
    user_agent = http_request.headers.get("user-agent")

    # Создаем запись сессии в БД
    try:
        session = UserSession(
            user_id=user_id,
            user_id_fk=user.id,
            username=user.username,
            session_token=session_token,
            token_hash=token_hash,
            started_at=datetime.utcnow(),
            last_activity=datetime.utcnow(),
            ip_address=ip_address,
            user_agent=user_agent,
            is_active="true"
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        session_id = session.id
        logger.info(f"✅ Пользователь {user.email} успешно авторизован (session_id={session_id})")
    except Exception as e:
        # Если ошибка при сохранении в БД, откатываем транзакцию
        db.rollback()
        # Логируем ошибку с деталями
        logger.error(f"⚠️ Ошибка при сохранении сессии в БД: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Не удалось сохранить пользовательскую сессию. Проверьте миграции и состояние БД.",
        ) from e

    set_auth_cookie(response, access_token)
    return LoginResponse(
        access_token=access_token,
        user_id=user_id,
        username=user.username,
        session_id=session_id
    )


@router.post("/logout")
async def logout(
    http_request: Request,
    response: Response,
    db: Session = Depends(get_db_session)
):
    """
    Завершает сессию пользователя.

    Args:
        http_request: HTTP запрос (для получения токена)
        db: Сессия БД

    Returns:
        Подтверждение выхода
    """
    # Получаем токен из заголовка
    authorization = http_request.headers.get("authorization")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется токен"
        )

    token = authorization.split(" ")[1]

    try:
        # Декодируем токен
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        session_token = payload.get("session_token")

        if session_token:
            # Находим сессию и помечаем как неактивную
            session = db.query(UserSession).filter(
                UserSession.session_token == session_token,
                UserSession.is_active == "true"
            ).first()

            if session:
                session.is_active = "false"
                session.ended_at = datetime.utcnow()
                db.commit()

        clear_auth_cookie(response)
        return {"message": "Выход выполнен успешно"}
    except JWTError:
        clear_auth_cookie(response)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невалидный токен"
        ) from None


@router.get("/me")
@router.get("/auth/me")
async def current_user_profile(
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Возвращает текущего пользователя, если bearer-токен и серверная сессия валидны."""
    return {
        "id": user.get("id"),
        "username": user.get("username"),
        "email": user.get("email"),
        "role": user.get("role"),
    }


@router.post("/session-cookie")
@router.post("/auth/session-cookie")
async def sync_navigation_cookie(
    request: Request,
    response: Response,
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Refresh the HttpOnly cookie used by normal navigation into mounted tools."""

    token = request_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Требуется токен",
        )
    set_auth_cookie(response, token)
    return {
        "ok": True,
        "user_id": user.get("id"),
    }


@router.get("/sessions")
async def get_sessions(
    user_id: str | None = None,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db_session)
):
    """
    Получает список сессий.

    Args:
        user_id: Фильтр по user_id (опционально)
        db: Сессия БД

    Returns:
        Список сессий
    """
    current_user_id = user.get("id")
    requested_user_id = user_id or current_user_id
    if not requested_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Пользователь не аутентифицирован",
        )
    if requested_user_id != current_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нет доступа к сессиям другого пользователя",
        )

    query = db.query(UserSession).filter(UserSession.user_id == requested_user_id)

    sessions = query.order_by(UserSession.started_at.desc()).limit(100).all()

    return {
        "sessions": [_session_public_payload(session) for session in sessions],
        "total": len(sessions)
    }


class RegisterRequest(BaseModel):
    """Запрос на регистрацию."""
    email: EmailStr
    username: str
    password: str


class ForgotPasswordRequest(BaseModel):
    """Запрос на восстановление пароля."""
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Запрос на сброс пароля."""
    token: str = Field(min_length=1, max_length=255)
    new_password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=1024)


@router.post("/register")
@limiter.limit("3/minute")
async def register(
    register_data: RegisterRequest,
    request: Request,
    db: Session = Depends(get_db_session)
):
    """Регистрация нового пользователя."""
    # Ограничение по домену
    email = _ensure_allowed_domain(register_data.email)

    # Проверяем, что email и username уникальны
    existing_user = db.query(User).filter(
        (User.email == email) | (User.username == register_data.username)
    ).first()

    if existing_user:
        if existing_user.email == email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email уже зарегистрирован"
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Имя пользователя уже занято"
            )

    # Создаем пользователя
    user = User(
        email=email,
        username=register_data.username,
        hashed_password=User.hash_password(register_data.password),
        is_active=True
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    # Отправляем приветственное письмо
    try:
        welcome_html = get_welcome_email_html(user.username)
        await send_email_async(
            to_email=user.email,
            subject="Добро пожаловать в Генератор учебных проектов!",
            html_body=welcome_html
        )
    except Exception as e:
        logger.warning(f"Не удалось отправить приветственное письмо: {e}")

    logger.info(f"✅ Новый пользователь зарегистрирован: {user.email}")

    return {"message": "Регистрация успешна", "user_id": user.id}


@router.post("/forgot-password")
@limiter.limit("3/hour")
async def forgot_password(
    forgot_data: ForgotPasswordRequest,
    request: Request,
    db: Session = Depends(get_db_session)
):
    """Запрос на восстановление пароля."""
    # Ограничение по домену (security best practice: не раскрываем, что именно не так)
    try:
        email = _ensure_allowed_domain(forgot_data.email)
    except HTTPException as exc:
        logger.info(f"📧 Запрос восстановления для запрещенного домена: {forgot_data.email} ({exc.detail})")
        return {"message": "Если email существует, на него отправлена инструкция"}

    user = db.query(User).filter(User.email == email).first()

    # Всегда возвращаем успех (security best practice)
    if not user:
        logger.info(f"📧 Запрос восстановления для несуществующего email: {forgot_data.email}")
        return {"message": "Если email существует, на него отправлена инструкция"}

    # Генерируем одноразовый токен и храним только его хеш.
    reset_token = secrets.token_urlsafe(32)
    reset_token_hash = hash_token(reset_token)
    expires_at = datetime.utcnow() + timedelta(hours=1)

    # Отзываем старые неиспользованные ссылки, чтобы активной была только последняя.
    stale_tokens = db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used.is_(False),
    ).all()
    for stale_token in stale_tokens:
        stale_token.used = True

    # Сохраняем хеш токена.
    reset_record = PasswordResetToken(
        user_id=user.id,
        token=reset_token_hash,
        expires_at=expires_at
    )
    db.add(reset_record)
    db.commit()

    # Формируем ссылку
    base_url = str(request.base_url).rstrip('/')
    reset_link = f"{base_url}/reset-password?token={reset_token}"

    # Отправляем email
    html_body = get_password_reset_email_html(reset_link, user.username)
    await send_email_async(
        to_email=user.email,
        subject="Восстановление пароля - Генератор учебных проектов",
        html_body=html_body
    )

    return {"message": "Если email существует, на него отправлена инструкция"}


@router.post("/reset-password")
@limiter.limit("5/minute")
async def reset_password(
    reset_data: ResetPasswordRequest,
    request: Request,
    db: Session = Depends(get_db_session)
):
    """Сброс пароля по токену."""
    token_hash = hash_token(reset_data.token)

    # Находим токен. Fallback на plaintext оставлен для старых ссылок до выкладки хеширования.
    reset_record = db.query(PasswordResetToken).filter(
        PasswordResetToken.token == token_hash,
        PasswordResetToken.used.is_(False),
        PasswordResetToken.expires_at > datetime.utcnow()
    ).first()
    if not reset_record:
        reset_record = db.query(PasswordResetToken).filter(
            PasswordResetToken.token == reset_data.token,
            PasswordResetToken.used.is_(False),
            PasswordResetToken.expires_at > datetime.utcnow()
        ).first()

    if not reset_record:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Неверный или истекший токен"
        )

    user = db.query(User).filter(User.id == reset_record.user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Пользователь не найден"
        )

    # Обновляем пароль и закрываем все активные сессии пользователя.
    now = datetime.utcnow()
    user.hashed_password = User.hash_password(reset_data.new_password)
    user.failed_login_attempts = 0
    user.locked_until = None
    reset_record.used = True
    active_sessions = db.query(UserSession).filter(
        or_(
            UserSession.user_id_fk == user.id,
            UserSession.user_id == f"user_{user.id}",
        ),
        UserSession.is_active == "true",
    ).all()
    for session in active_sessions:
        session.is_active = "false"
        session.ended_at = now
    db.commit()

    logger.info(f"✅ Пароль изменен для пользователя: {user.email}")

    return {"message": "Пароль успешно изменен"}
