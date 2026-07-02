#!/usr/bin/env python3
"""
Скрипт для проверки наличия всех таблиц в базе данных.

Использование:
    python check_db_tables.py
"""

import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import SQLAlchemyError

# Загружаем переменные окружения
load_dotenv()

# Ожидаемые таблицы из моделей
EXPECTED_TABLES = {
    "logs": "Логи приложения",
    "users": "Пользователи",
    "password_reset_tokens": "Токены сброса пароля",
    "user_sessions": "Сессии пользователей",
    "request_logs": "Логи запросов",
    "generation_results": "Результаты генерации",
    "rubric_results": "Результаты рубрик",
    "report_results": "Результаты отчетов",
    "alembic_version": "Версия миграций Alembic",
}

def check_database_tables():
    """Проверяет наличие всех таблиц в базе данных."""

    # Получаем URL БД
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("❌ Ошибка: DATABASE_URL не установлен в переменных окружения")
        print("   Установите переменную DATABASE_URL с URL подключения к PostgreSQL")
        sys.exit(1)

    try:
        # Создаем подключение
        print(f"🔌 Подключение к БД: {database_url.split('@')[1] if '@' in database_url else 'скрыто'}...")
        engine = create_engine(database_url)

        # Создаем инспектор
        inspector = inspect(engine)

        # Получаем список всех таблиц в БД
        existing_tables = set(inspector.get_table_names())

        print("\n📊 Проверка таблиц в базе данных...")
        print(f"   Найдено таблиц в БД: {len(existing_tables)}")
        print(f"   Ожидается таблиц: {len(EXPECTED_TABLES)}\n")

        # Проверяем каждую ожидаемую таблицу
        missing_tables = []
        found_tables = []

        for table_name, description in EXPECTED_TABLES.items():
            if table_name in existing_tables:
                print(f"✅ {table_name:30} - {description}")
                found_tables.append(table_name)
            else:
                print(f"❌ {table_name:30} - {description} - ОТСУТСТВУЕТ!")
                missing_tables.append(table_name)

        # Показываем дополнительные таблицы (если есть)
        extra_tables = existing_tables - set(EXPECTED_TABLES.keys())
        if extra_tables:
            print("\n📋 Дополнительные таблицы в БД (не в списке ожидаемых):")
            for table in sorted(extra_tables):
                print(f"   ℹ️  {table}")

        # Итоговый результат
        print(f"\n{'='*60}")
        if missing_tables:
            print(f"❌ ПРОБЛЕМА: Отсутствуют {len(missing_tables)} таблиц:")
            for table in missing_tables:
                print(f"   - {table}")
            print("\n💡 Решение: Запустите миграции Alembic:")
            print("   alembic upgrade head")
            return False
        else:
            print("✅ ВСЕ ТАБЛИЦЫ НА МЕСТЕ!")
            print(f"   Найдено: {len(found_tables)}/{len(EXPECTED_TABLES)}")
            return True

    except SQLAlchemyError as e:
        print(f"❌ Ошибка подключения к БД: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Неожиданная ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        if 'engine' in locals():
            engine.dispose()

if __name__ == "__main__":
    success = check_database_tables()
    sys.exit(0 if success else 1)

