# Тестирование

Тесты должны защищать контракты, а не только покрывать строки. Для LLM-системы важны regression cases, schema validation и проверки UI-contract.

## Быстрый запуск

```powershell
python -m pytest
```

Если нужно проверить только последние UI/Markdown изменения:

```powershell
python -m pytest tests/utils/test_static_ui_contracts.py -q
```

## Ключевые наборы

| Набор | Команда | Что защищает |
|---|---|---|
| API/auth | `python -m pytest tests/api -q` | Сессии, доступ, download ownership, password reset. |
| Flow | `python -m pytest tests/content_gen/test_flow_handlers.py tests/content_gen/test_node_contracts.py -q` | Контракты нод и runtime flow. |
| Regeneration | `python -m pytest tests/content_gen/test_regeneration_pipeline.py tests/api/services/test_regeneration_service.py -q` | Scoped patches, deterministic apply, validation report. |
| Rubric | `python -m pytest tests/validators/rubric -q` | Проверка README по критериям. |
| Translation | `python -m pytest tests/api/routers/test_readme_translate_documents.py tests/subtitles/test_translation_language_contracts.py -q` | Документы, языки, письменность, статусы. |
| Static UI | `python -m pytest tests/utils/test_static_ui_contracts.py -q` | Наличие JS/CSS contracts, кнопки, маршруты, vendors. |

## Что проверять перед деплоем

Минимальный smoke:

```powershell
python -m pytest tests/api/routers/test_health.py -q
python -m pytest tests/api/routers/test_auth_password_reset.py -q
python -m pytest tests/api/routers/test_readme_translate_documents.py -q
python -m pytest tests/utils/test_static_ui_contracts.py -q
```

Перед изменением генерации:

```powershell
python -m pytest tests/content_gen tests/agents tests/validators -q
```

Перед изменением методологического режима:

```powershell
python -m pytest tests/methodology tests/api/services/test_methodology_review_service.py -q
```

## Eval-наборы

Для перегенерации и LLM-поведения нужны отдельные eval cases:

- правка одной секции не портит соседние;
- добавление/удаление главы обновляет содержание и ссылки;
- rubric не ухудшается без явного объяснения;
- protected blocks не попадают в текст;
- таблицы, формулы и Mermaid сохраняются при переводе.

Файлы eval harness находятся в `content_gen/evaluation/` и `tests/evaluation/`.

## Практика разработки

- Добавляйте regression test на каждый исправленный production bug.
- Не тестируйте LLM как черный ящик там, где можно проверить typed contract.
- Для UI-изменений проверяйте не только CSS, но и наличие обработчиков действий.
- Для файловых операций используйте временные директории и не пишите за пределы workspace.
