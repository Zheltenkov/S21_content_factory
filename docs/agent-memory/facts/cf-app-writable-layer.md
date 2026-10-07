---
name: cf-app-writable-layer
description: Боевой cf-app работает на коде из записываемого слоя контейнера, а не из образа; пересоздание откатит бой на июль 2026
metadata:
  type: gotcha
---

Контейнер `cf-app` на 77.110.123.227 поднят из образа `deploy-app`
(`sha256:1f0a2c5fc577`, собран 2026-07-14), но работает не на коде образа.
`docker diff cf-app` на 2026-10-07 даёт 29657 записей. В записываемом слое лежит
месяц с лишним правок, доставленных через `docker cp`: добавленные
`api/routers/rpd.py`, `audit/activity.py`, `audit/overview.py`, `audit/branch.py`,
`audit/profanity.py`, `audit/topic_coherence.py`, `platform/llm/openai_compat.py`,
`audit/data/dwh_*.csv`; изменённые `api/main.py`, `api/routers/auditor.py`,
`api/routers/curriculum.py`, `audit/web_rendering.py`, `audit/templates/report.js`,
`generation/embeddings.py`, `generation/subtitles/pipeline.py`, шаблоны
`catalog/viewer/templates/*`. Пакет ffmpeg тоже доставлен вживую: в контейнере
`/usr/bin/ffmpeg` есть, в образе — нет, строки `ffmpeg` в серверном `Dockerfile`
нет.

Там же, в записываемом слое, лежат все результаты аудита:
`/app/src/content_factory/.tmp/auditor/ui/user_3/` — 113 веток, 711 МБ. Том
`deploy_cf_appdata -> /data` при этом почти пуст (`/data/artifacts` без файлов).
Копия аудитов снята 2026-10-07 на хост: `/opt/backups/auditor/auditor_20261007`.

**Почему:** `docker compose up --force-recreate` и любой `docker compose up` с
изменённым образом создают новый контейнер с чистым записываемым слоем. Бой
откатится на июльский код: без РПД, без функций аудитора, без правки доступов к
провайдеру, без ffmpeg, без 113 аудитов. Обычный `docker restart` слой сохраняет
и безопасен.

**Как применять:** `cf-app` не пересоздавать. Правки кода доставлять `docker cp`
плюс `docker restart`, как делалось до сих пор. Переменные окружения так поменять
нельзя — `env_file` вшивается при создании контейнера, поэтому смена модели в
`/opt/s21_content_factory/.env` подействует только при следующем пересоздании.
Прежде чем пересоздавать, нужен полноценный релиз: собрать образ из текущего
исходника (сверив его с содержимым записываемого слоя — часть кода в git может
отсутствовать) и вернуть аудиты из резервной копии.

Связано: [[model-gpt6-sol]], [[project-lab-production]]
