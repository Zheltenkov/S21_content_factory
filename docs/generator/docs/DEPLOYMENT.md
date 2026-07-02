# Деплой

Документ описывает production-развертывание без секретов. Значения ключей, паролей и токенов должны храниться только в `.env` на сервере или в secret store.

## Требования

- Python 3.10+.
- PostgreSQL 14+.
- ffmpeg для видео-перевода.
- Доступ к LLM provider.
- systemd или другой process manager.
- Reverse proxy с HTTPS для публичного контура.

## Переменные окружения

Минимально обязательные:

```env
HOST=0.0.0.0
PORT=8000
RELOAD=false
DATABASE_URL=postgresql://user:password@localhost:5432/content_generator
DB_AUTO_CREATE_TABLES=false
JWT_SECRET_KEY=change_me
ALLOWED_EMAIL_DOMAIN=21-school.ru
CORS_ORIGINS=https://your-domain.example
LLM_PROVIDER=polza
POLZA_AI_API_KEY=...
POLZA_AI_BASE_URL=https://polza.ai/api/v1
POLZA_AI_MODEL=openai/gpt-5.4-mini
```

Для видео:

```env
MAX_VIDEO_SIZE_BYTES=524288000
VIDEO_MAX_CONCURRENT_JOBS=2
WHISPER_ASR_MODEL=whisper-1
```

Для observability:

```env
OBSERVABILITY_EXPORTERS=
OTEL_ENABLED=false
LANGFUSE_ENABLED=false
```

## Первый запуск

```bash
git clone <repository-url>
cd Content_generator_ver1
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
python run.py
```

Healthcheck:

```bash
curl http://127.0.0.1:8000/api/v1/health
```

## Обновление

```bash
git fetch origin
git checkout develop
git pull --ff-only origin develop
source .venv/bin/activate
python -m pip install -r requirements.txt
alembic upgrade head
python -m pytest tests/utils/test_static_ui_contracts.py -q
sudo systemctl restart content-generator
sudo systemctl status content-generator --no-pager
```

## systemd unit

```ini
[Unit]
Description=Content Generator
After=network.target postgresql.service

[Service]
WorkingDirectory=/opt/Content_generator_ver1
EnvironmentFile=/opt/Content_generator_ver1/.env
ExecStart=/opt/Content_generator_ver1/.venv/bin/python run.py
Restart=always
RestartSec=5
User=content-generator
Group=content-generator

[Install]
WantedBy=multi-user.target
```

## База данных

- В production `DB_AUTO_CREATE_TABLES=false`.
- Все изменения схемы проходят через Alembic.
- Перед destructive reset БД нужен явный backup или осознанное подтверждение владельца сервера.

## Безопасность

- `.env` не коммитится.
- `JWT_SECRET_KEY` должен быть длинным случайным значением.
- Регистрация ограничивается доменом `ALLOWED_EMAIL_DOMAIN`.
- Download endpoints проверяют владельца результата.
- Upload endpoints используют allowlist расширений и лимиты размера.
- Для публичного контура нужен HTTPS reverse proxy.

## Post-deploy checklist

1. `/api/v1/health` отвечает `200`.
2. Login/register работают для разрешенного домена.
3. Загрузка curriculum CSV работает.
4. Проверка README принимает файл и показывает критерии.
5. Переводчик принимает документ.
6. Видео-перевод принимает файл в пределах лимита.
7. Архив генерации содержит `README.md` и `check-list.yml`.
8. В логах нет raw secret values.
