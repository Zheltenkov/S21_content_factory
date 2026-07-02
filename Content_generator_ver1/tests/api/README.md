# Тесты API компонентов

Этот каталог содержит тесты для критичных компонентов API, включая безопасность, валидацию, кэширование и контроль доступа.

## Структура тестов

```
tests/api/
├── utils/                    # Тесты утилит
│   ├── test_file_validation.py    # Валидация файлов
│   ├── test_security.py           # Утилиты безопасности
│   ├── test_result_cache.py      # Кэш результатов
│   └── test_session_cache.py     # Кэш сессий
├── middleware/               # Тесты middleware
│   ├── test_security_headers.py  # Security Headers
│   └── test_ip_rate_limit.py    # IP Rate Limiting
└── routers/                  # Тесты роутеров
    ├── test_download_access_control.py  # Контроль доступа к скачиванию
    └── test_admin_access_control.py     # Контроль доступа админа
```

## Запуск тестов

### Все тесты API
```bash
pytest tests/api/ -v
```

### Конкретный модуль
```bash
pytest tests/api/utils/ -v
pytest tests/api/middleware/ -v
pytest tests/api/routers/ -v
```

### Конкретный файл
```bash
pytest tests/api/utils/test_file_validation.py -v
```

### С покрытием
```bash
pytest tests/api/ --cov=api --cov-report=html
```

## Типы тестов

### Unit-тесты
- **file_validation**: Валидация размера, расширений, защиты от path traversal
- **security**: Защита от timing attacks, генерация токенов, хеширование
- **result_cache**: LRU кэш, TTL, управление статусами
- **session_cache**: Кэширование сессий, TTL, инвалидация

### Middleware тесты
- **security_headers**: Добавление security headers, HSTS для HTTPS
- **ip_rate_limit**: Rate limiting по IP, исключения, заголовки

### Интеграционные тесты
- **download_access_control**: Проверка прав доступа к результатам
- **admin_access_control**: Проверка роли администратора

## Зависимости

Тесты требуют:
- `pytest>=7.4.0`
- `pytest-asyncio>=0.21.0`
- `pytest-mock>=3.11.0`

Установка:
```bash
pip install -r requirements.txt
```

## Примечания

- Тесты используют моки для изоляции от внешних зависимостей (БД, файловая система)
- Асинхронные тесты помечены декоратором `@pytest.mark.asyncio`
- Некоторые тесты используют `monkeypatch` для изменения env переменных

