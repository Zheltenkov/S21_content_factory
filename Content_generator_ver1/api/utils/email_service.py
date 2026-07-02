"""Сервис для отправки email."""

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from api.utils.logger import get_logger

logger = get_logger("email")

# Разрешенный домен для отправки писем (по умолчанию 21-school.ru)
ALLOWED_EMAIL_DOMAIN = os.getenv("ALLOWED_EMAIL_DOMAIN", "21-school.ru")

# SMTP настройки из env
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"

# Для разработки можно использовать mock
ENABLE_EMAIL = os.getenv("ENABLE_EMAIL", "true").lower() == "true"


async def send_email_async(
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str | None = None
) -> bool:
    """
    Асинхронно отправляет email.
    
    Args:
        to_email: Email получателя
        subject: Тема письма
        html_body: HTML содержимое
        text_body: Текстовое содержимое (опционально)
        
    Returns:
        True если отправлено успешно, False в противном случае
    """
    # Ограничение по домену получателя
    normalized_to = (to_email or "").strip().lower()
    if not normalized_to.endswith(f"@{ALLOWED_EMAIL_DOMAIN}"):
        logger.warning(
            "⚠️ Попытка отправки email на запрещенный домен: %s (allowed: @%s)",
            normalized_to,
            ALLOWED_EMAIL_DOMAIN,
        )
        return False
    if not ENABLE_EMAIL:
        logger.info(f"[MOCK] Email to {to_email}: {subject}")
        logger.debug(f"[MOCK] Body: {html_body[:200]}...")
        return True

    if not SMTP_USER or not SMTP_PASSWORD:
        logger.warning("⚠️ SMTP не настроен, email не отправлен")
        return False

    try:
        import asyncio

        from api.core.executors import general_executor

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            general_executor,
            _send_email_sync,
            to_email,
            subject,
            html_body,
            text_body
        )
        logger.info(f"✅ Email отправлен: {to_email} - {subject}")
        return True
    except Exception as e:
        logger.error(f"❌ Ошибка отправки email: {e}", exc_info=True)
        return False


def _send_email_sync(
    to_email: str,
    subject: str,
    html_body: str,
    text_body: str | None = None
) -> None:
    """Синхронная отправка email."""
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = SMTP_FROM
    msg['To'] = to_email

    if text_body:
        msg.attach(MIMEText(text_body, 'plain'))
    msg.attach(MIMEText(html_body, 'html'))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        if SMTP_USE_TLS:
            server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)


def get_password_reset_email_html(reset_link: str, username: str) -> str:
    """Генерирует HTML для письма восстановления пароля."""
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
    </head>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; margin: 0; padding: 0;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f5f5f5;">
            <div style="background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                <h2 style="color: #764ba2; margin-bottom: 20px;">Восстановление пароля</h2>
                <p style="margin-bottom: 15px;">Здравствуйте, {username}!</p>
                <p style="margin-bottom: 15px;">Вы запросили восстановление пароля для аккаунта в <strong>Генераторе учебных проектов</strong>.</p>
                <p style="margin: 30px 0;">
                    <a href="{reset_link}" 
                       style="background: #764ba2; color: white; padding: 12px 24px; 
                              text-decoration: none; border-radius: 6px; display: inline-block; font-weight: bold;">
                        Восстановить пароль
                    </a>
                </p>
                <p style="color: #666; font-size: 0.9em; margin-top: 30px; padding-top: 20px; border-top: 1px solid #eee;">
                    <strong>Важно:</strong><br>
                    • Ссылка действительна в течение 1 часа<br>
                    • Если вы не запрашивали восстановление пароля, проигнорируйте это письмо<br>
                    • Для безопасности не пересылайте эту ссылку другим лицам
                </p>
            </div>
        </div>
    </body>
    </html>
    """


def get_welcome_email_html(username: str) -> str:
    """Генерирует HTML для приветственного письма."""
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
    </head>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; margin: 0; padding: 0;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px; background: #f5f5f5;">
            <div style="background: white; padding: 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                <h2 style="color: #764ba2; margin-bottom: 20px;">Добро пожаловать!</h2>
                <p style="margin-bottom: 15px;">Здравствуйте, {username}!</p>
                <p style="margin-bottom: 15px;">Ваш аккаунт в <strong>Генераторе учебных проектов</strong> успешно создан.</p>
                <p style="margin-bottom: 15px;">Теперь вы можете использовать все возможности системы для генерации учебного контента.</p>
                <p style="color: #666; font-size: 0.9em; margin-top: 30px; padding-top: 20px; border-top: 1px solid #eee;">
                    Если у вас возникнут вопросы, обратитесь к администратору системы.
                </p>
            </div>
        </div>
    </body>
    </html>
    """

