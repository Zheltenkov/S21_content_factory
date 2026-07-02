"""
utils/excel_io.py

Утилиты для работы с Excel файлами.
"""

import io
from typing import Any

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False


def excel_template() -> io.BytesIO:
    """
    Создаёт Excel шаблон для спецификации проекта (как в старом проекте).
    
    Returns:
        BytesIO буфер с Excel файлом
    """
    if not PANDAS_AVAILABLE:
        raise ImportError("Установите pandas: pip install pandas openpyxl")

    template_data = {
        "Параметр": [
            "language",
            "project_type",
            "thematic_block",
            "audience_level",
            "required_tools",
            "sjm",
            "title_seed",
            "project_description",
            "learning_outcomes",
            "skills",
            "group_size",
            "bonus_wish",
            "repo_base_url",
            "repo_path_template"
        ],
        "Значение": [
            "ru",
            "group",
            "PjM",
            "Начальный",
            "Figma, Git",
            "Ты работаешь младшим менеджером проекта и получаешь рабочий кейс с ограничениями, командой и сроками.",
            "Введение в проектную деятельность",
            "Введение в проектную деятельность, что такое IT-проект. Роли и функции менеджера проекта.",
            "Анализировать и классифицировать проекты и процессы\nВыделять ключевые параметры проектной деятельности",
            "Проектное мышление\nАналитическое мышление",
            "3",
            "",
            "",
            "repo/part-03/task-{num:02d}/README.md"
        ],
        "Описание": [
            "Язык проекта (ru, en, kg, uz)",
            "Тип проекта (individual, group)",
            "Кодовое обозначение тематического блока (BSA, Cb, DO, PjM, QA)",
            "Уровень аудитории (base, advanced, etc.)",
            "Обязательные инструменты через запятую",
            "Сторителлинг/кейс проекта: роль, ситуация, ограничения",
            "Название проекта",
            "Краткое описание проекта",
            "Образовательные результаты (по одному в строке)",
            "Получаемые навыки (по одному в строке)",
            "Количество человек в группе (только для групповых проектов, 2-10)",
            "Дополнение к бонусному заданию (опционально)",
            "Базовый URL репозитория (опционально, например: https://github.com/school21/project-name)",
            "Шаблон пути в репозитории (опционально, например: repo/part-03/task-{num:02d}/README.md)"
        ]
    }

    df = pd.DataFrame(template_data)
    output = io.BytesIO()

    # Используем ExcelWriter для создания файла
    # mode='w' - режим записи (по умолчанию, но явно указываем для ясности)
    try:
        with pd.ExcelWriter(output, engine='openpyxl', mode='w') as writer:
            df.to_excel(writer, index=False, sheet_name='Спецификация')
            # writer.save() вызывается автоматически при выходе из контекста
    except Exception as e:
        raise ValueError(f"Ошибка при создании Excel файла: {str(e)}")

    # После выхода из контекста writer автоматически закрыт и данные записаны
    # Возвращаем указатель в начало буфера
    output.seek(0)

    # Проверяем, что файл не пустой и содержит данные
    file_size = len(output.getvalue())
    if file_size == 0:
        raise ValueError("Созданный Excel файл пуст")

    if file_size < 1000:  # Минимальный размер для валидного Excel файла
        raise ValueError(f"Созданный Excel файл слишком мал ({file_size} байт), возможно поврежден")

    return output


def excel_to_json(file_bytes: bytes) -> list[dict[str, Any]]:
    """
    Преобразует загруженный Excel → JSON-спецификации.
    
    Поддерживает два формата:
    1. Старый формат (program_title, project_title, etc.)
    2. Новый формат (Параметр/Значение из шаблона)
    
    Args:
        file_bytes: Байты Excel файла
        
    Returns:
        Список словарей с данными проектов
    """
    if not PANDAS_AVAILABLE:
        raise ImportError("Установите pandas: pip install pandas openpyxl")

    df = pd.read_excel(io.BytesIO(file_bytes), engine='openpyxl')

    # Проверяем формат: если есть колонка "Параметр", это новый формат
    if "Параметр" in df.columns:
        # Новый формат (шаблон)
        result = {}
        value_col = "Значение" if "Значение" in df.columns else df.columns[1]

        for _, row in df.iterrows():
            key = str(row["Параметр"]).strip()
            if not key or key == "nan":
                continue
            if key in {"evaluation_criteria", "include_static_checklist"}:
                # Устаревшие настройки больше не участвуют в генерации:
                # P2P-критерии формируются внутри каждой практической задачи.
                continue

            value = row[value_col] if value_col else None

            # Обрабатываем специальные поля
            if key in ["learning_outcomes", "skills", "required_tools"]:
                if pd.isna(value) or (isinstance(value, str) and not value.strip()):
                    result[key] = []
                else:
                    value_str = str(value)
                    if "\n" in value_str:
                        parsed_list = [x.strip() for x in value_str.split("\n") if x.strip()]
                    else:
                        parsed_list = [x.strip() for x in value_str.split(",") if x.strip()]
                    result[key] = parsed_list
            elif key == "group_size":
                if pd.isna(value) or (isinstance(value, str) and not value.strip()):
                    result[key] = None
                else:
                    try:
                        result[key] = int(float(value))
                    except (ValueError, TypeError):
                        result[key] = None
            elif key in ["bonus_wish", "repo_base_url", "repo_path_template"]:
                if pd.isna(value) or (isinstance(value, str) and not value.strip()):
                    result[key] = None
                else:
                    result[key] = str(value)
            else:
                if pd.isna(value) or (isinstance(value, str) and not value.strip()):
                    result[key] = None
                else:
                    result[key] = str(value)

        # Обратная совместимость
        if "track" in result and "thematic_block" not in result:
            result["thematic_block"] = result["track"]

        if "project_type" in result:
            if result["project_type"] == "индивидуальный":
                result["project_type"] = "individual"
            elif result["project_type"] == "групповой":
                result["project_type"] = "group"

        # Маппинг старых полей на новые
        if "project_title" in result and "title_seed" not in result:
            result["title_seed"] = result["project_title"]

        return [result]
    else:
        # Старый формат (простой список записей)
        records = df.to_dict(orient="records")
        result = []
        for r in records:
            lo_raw = r.get("learning_outcomes", "")
            lo_list = [x.strip() for x in str(lo_raw).split("\n") if x.strip()] if lo_raw else []

            skills_raw = r.get("skills", "")
            skills_list = [x.strip() for x in str(skills_raw).split(",") if x.strip()] if skills_raw else []

            item = {
                "thematic_block": r.get("thematic_block", r.get("track", "")),
                "title_seed": r.get("project_title", r.get("title_seed", "")),
                "project_description": r.get("project_description", ""),
                "learning_outcomes": lo_list,
                "skills": skills_list,
            }

            # Добавляем остальные поля, если есть
            for key in ["language", "project_type", "audience_level", "required_tools",
                       "group_size", "bonus_wish", "sjm", "repo_base_url",
                       "repo_path_template"]:
                if key in r:
                    if key == "required_tools":
                        raw_value = "" if pd.isna(r[key]) else str(r[key])
                        item[key] = [
                            value.strip()
                            for value in raw_value.replace("\n", ",").split(",")
                            if value.strip()
                        ]
                    else:
                        item[key] = r[key]

            result.append(item)

        return result


def json_to_excel(data: list[dict[str, Any]]) -> io.BytesIO:
    """
    Сохраняет готовые проекты в Excel.
    
    Args:
        data: Список словарей с данными проектов
        
    Returns:
        BytesIO буфер с Excel файлом
    """
    if not PANDAS_AVAILABLE:
        raise ImportError("Установите pandas: pip install pandas openpyxl")

    rows = []
    for d in data:
        rows.append({
            "program_title": d.get("program_title", ""),
            "thematic_block": d.get("thematic_block", ""),
            "project_title": d.get("project_title", ""),
            "project_description": d.get("project_description", ""),
            "learning_outcomes": "\n".join(d.get("learning_outcomes", [])),
            "skills": ", ".join(d.get("skills", [])),
            "sjm": d.get("sjm", ""),
        })

    df = pd.DataFrame(rows)

    buffer = io.BytesIO()
    df.to_excel(buffer, index=False, engine='openpyxl')
    buffer.seek(0)
    return buffer
