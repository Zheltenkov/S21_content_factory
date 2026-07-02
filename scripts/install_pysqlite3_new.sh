#!/bin/bash
# Скрипт для установки pysqlite3 с SQLite >= 3.35.0
# Для использования на Ubuntu 20.04 и других старых системах

set -e

echo "🔧 Установка зависимостей для сборки pysqlite3..."

# Установка зависимостей
apt-get update
apt-get install -y \
    build-essential \
    python3-dev \
    wget \
    unzip

echo "📥 Скачивание SQLite 3.45.0 (последняя стабильная версия)..."

# Создаем временную директорию
TMPDIR=$(mktemp -d)
cd "$TMPDIR"

# Скачиваем SQLite 3.45.0
SQLITE_VERSION="3450000"
SQLITE_URL="https://www.sqlite.org/2024/sqlite-autoconf-${SQLITE_VERSION}.tar.gz"

wget "$SQLITE_URL" -O sqlite.tar.gz
tar -xzf sqlite.tar.gz
cd sqlite-autoconf-${SQLITE_VERSION}

echo "🔨 Компиляция SQLite..."

# Компилируем SQLite
./configure --prefix=/usr/local
make -j$(nproc)
make install

# Обновляем библиотеки
ldconfig

echo "📦 Установка pysqlite3 из исходников с новой версией SQLite..."

# Возвращаемся в директорию проекта
cd /opt/Content_generator_ver1
source venv/bin/activate

# Удаляем старые версии
pip uninstall pysqlite3 pysqlite3-binary -y || true

# Устанавливаем pysqlite3 из исходников
# Указываем путь к новой версии SQLite
export SQLITE3_LIBRARY=/usr/local/lib/libsqlite3.so
export SQLITE3_INCLUDE_DIR=/usr/local/include

pip install pysqlite3 --no-binary pysqlite3 --no-cache-dir

echo "✅ Проверка версии SQLite..."

python -c "import pysqlite3; print(f'✅ SQLite version: {pysqlite3.sqlite_version_info}')"

# Очистка
rm -rf "$TMPDIR"

echo "✅ Установка завершена!"

