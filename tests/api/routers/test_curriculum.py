"""
Тесты для API endpoint загрузки и парсинга учебного плана (УП).
"""

from io import BytesIO

import pytest
from fastapi.testclient import TestClient

from content_factory.api.dependencies import get_current_user
from content_factory.api.main import app


# Мок для авторизации
def mock_get_current_user():
    return {"id": "test-user-id", "username": "test_user", "role": "user"}


@pytest.fixture
def client():
    """Тестовый клиент с моком авторизации."""
    app.dependency_overrides[get_current_user] = mock_get_current_user
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_curriculum_router_exposes_persisted_plan_routes():
    """Роутер отдает УП из общего каталога для генератора."""

    from fastapi.routing import APIRoute

    from content_factory.api.routers.curriculum import router

    routes = {
        (route.path, method)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }

    assert ("/curriculum/plans", "GET") in routes
    assert ("/curriculum/plans/sync", "POST") in routes
    assert ("/curriculum/plans/{source_id}", "GET") in routes


def test_spravochnik_plan_payload_converts_to_generator_contract():
    """Payload УП из справочника превращается в блоки и проекты генератора."""

    from content_factory.api.integrations.spravochnik_curriculum_sync import convert_spravochnik_plan_to_generator_curriculum

    data = convert_spravochnik_plan_to_generator_curriculum(
        {
            "id": 42,
            "title": "Data Science",
            "status": "built",
            "blocks": [
                {
                    "block_index": 1,
                    "title": "Блок 1. Python и данные",
                    "goal": "Работать с данными\nСтроить пайплайны",
                    "rows": [
                        {
                            "row_number": 3,
                            "project_index_in_block": 1,
                            "project_name": "Анализ датасета",
                            "project_summary": "Участник загружает и исследует данные.",
                            "learning_outcomes": "Читать CSV\nПроверять качество данных",
                            "skills_list": "Python, Pandas",
                            "required_tools": "Jupyter, Git",
                            "delivery_format": "групповой",
                            "group_size": "3-4",
                            "effort_hours": 16,
                            "effort_days": "2,5",
                            "xp": 120,
                            "completion_percent": 75,
                            "storytelling": "Рабочий кейс аналитика.",
                            "materials": "materials/data.md",
                            "validation_criteria": "Проверить notebook и отчет.",
                            "platform_project_name": "DSB1_Dataset",
                            "artifact_links": "https://gitlab.example/project",
                        }
                    ],
                }
            ],
        }
    )

    assert data["source_plan_id"] == 42
    assert data["direction_code"] == "DS"
    assert data["blocks"][0]["name"] == "Блок 1. Python и данные"
    assert data["blocks"][0]["goals"] == ["Работать с данными", "Строить пайплайны"]
    project = data["blocks"][0]["projects"][0]
    assert project["order"] == 1
    assert project["title"] == "Анализ датасета"
    assert project["format"] == "group"
    assert project["group_size"] == 4
    assert project["learning_outcomes"] == ["Читать CSV", "Проверять качество данных"]
    assert project["skills"] == ["Python", "Pandas"]
    assert project["required_tools"] == ["Jupyter", "Git"]
    assert project["workload_hours"] == 16.0
    assert project["workload_days"] == 2.5
    assert project["xp"] == 120
    assert project["storytelling_type"] == "sjm"
    assert project["additional_materials"] == "materials/data.md"
    assert project["expert_notes"] == "Проверить notebook и отчет."


# Пример CSV данных учебного плана
SAMPLE_CSV_CONTENT = """Тематический блок;Цели блока;№;Название проекта;Краткое описание проекта;Что нужно разработать эксперту;Образовательные результаты (знает, понимает, умеет);SJM (описание ситуации/кейса, с которым сталкивается участник, сторителлинг или моделирование среды);Формат;Дополнительные материалы;Кол-во в группе;Трудоемкость, астр.часы;Трудоемкость, дни;Общая трудоемкость, дни;XP за проект;% прохождения проекта;Необходимое ПО/веб;Название проекта на платформе и в Gitlab;Ссылки на GitLab/Google docs
Блок 1. Введение в проектную деятельность;Понятие проекта и процесса
Основы общего менеджмента;1;Проекты и процессы в управлении;Введение в проектный менеджмент, определение IT-проектов.;Минорные правки;Анализировать проекты
Выделять ключевые параметры;Ты — новый проджект в IT-компании;;индивидуальный;;;12;4,08;8;120;;;PjM1_ProjPM;https://platform.example.com/1
;2;Жизненный цикл IT-проекта;Описание жизненного цикла проекта.;Дописать про DevOps;Анализировать жизненный цикл
Выделять ключевые этапы;;индивидуальный;;;12;4,08;8;120;;;PjM2_LifeCycle;https://platform.example.com/2
Блок 2. Подготовка проекта;Роль этапа сбора требований;5;Сбор требований;Обучение сбору и валидации требований.;Блок про ТЗ;Применять техники сбора требований
Формулировать требования;;индивидуальный;;;16;5,44;9;160;;;PjM5_ReqGather;https://platform.example.com/5
;6;Анализ продукта;Основы методов анализа продукта.;Переписать про PM;Формулировать гипотезы
Проводить исследования;;групповой;;3-4;20;6,8;10;200;;;PjM6_ProAnalysis;https://platform.example.com/6
"""


REALISTIC_COMMA_CSV_CONTENT = """  ,Цели блока,№ ,Название проекта,Краткое описание проекта,"Образовательные результаты (знает, понимает, умеет)",Список навыков,Уровень аудитории,Обязательные инструменты (через запятую),Сторителлинг,Формат,Кол-во в группе,"Трудоемкость, астр.часы","Трудоемкость, дни","Общая трудоемкость, дни",XP за проект,Название проекта на платформе и в Gitlab,Ссылки на GitLab/Google docs
Название всего блока (если делим на блоки),- цели всего блока (чему научим),,Название проекта,"Краткое описание, что представляет собой проект и задания в нем","что узнает и чему научится участник конкретно в этом проекте,
используем таксономию Блума","список навыков, которые осваивает пир в этом проекте",Уровень подготовки участника,"Miro, Google Docs","Кейс, роль и рабочая ситуация проекта",,,344,"116,96",,1760,очень важно придерживаться правил именования проектов,ссылки на готовые проекты
"Блок 1.
Введение в проектную деятельность","Понятие проекта и процесса, их отличия
Основы общего менеджмента",1,Проекты и процессы в управлении,"Введение в проектный менеджмент, определение IT-проектов.","Анализировать и классифицировать проекты и процессы
Выделять ключевые параметры проектной деятельности","Project planning, Analytical thinking",Начальный,"Miro, Google Docs","Ты начинающий project manager и разбираешь первый рабочий кейс команды.",индивидуальный,,12,"4,08",8,120,PjM1_ProjPM,https://platform.example.com/1
,"Этапы жизненного цикла IT-проекта
Задачи и специфика каждого этапа",2,Жизненный цикл IT-проекта,"Описание жизненного цикла проекта, специфика IT-проектов.","Анализировать жизненный цикл цифрового продукта
Выделять ключевые этапы","Project planning, Requirements Analysis",Начальный,"GitLab, draw.io","Ты сопровождаешь небольшой IT-проект от идеи до релиза.",индивидуальный,,12,"4,08",8,120,PjM2_LifeCycle,https://platform.example.com/2
Блок 2. Подготовка проекта,"Роль этапа сбора требований в управлении проектом
Техники сбора требований",5,Сбор требований,"Обучение сбору и валидации требований.","Применять техники сбора требований
Формулировать требования","Requirements Analysis",Средний,"Miro, Google Docs","Ты готовишь интервью с заказчиком и собираешь спорные требования.",групповой,3-4,20,"6,8",10,200,PjM5_ReqGather,https://platform.example.com/5
"""


PROGRAM_TEMPLATE_COMMA_CSV_CONTENT = """Тематический блок,Цели блока,№ ,Название контентной единицы,Краткое описание,Образовательные результаты,Образовательные результаты,Образовательные результаты,Необходимое ПО,Дополнительные материалы для генерации,Сторителлинг,Формат,Кол-во в группе,"Трудоемкость, астр.часы","Трудоемкость, дни","Общая трудоемкость, дни",XP за проект,% прохождения проекта,Количество p2p проверок,Список навыков,Название проекта на платформе и в Gitlab,Ссылки на GitLab
Название всего блока (если делим на блоки),- цели всего блока (чему научим),,Название проекта,Краткое описание содержания проекта,что узнает участник,что умеет участник,какой навык приобретет участник,ПО,Материалы,Кейс,Формат,Кол-во,344,"116,96",,1760,75%,2,Навыки,очень важно придерживаться правил именования проектов,ссылки на готовые проекты
"Роль и функции ИТ-аналитика","Жизненный цикл
Декомпозиция",,BSA00_Decomposition,"В этом проекте участник изучает ключевые роли в разработке ИТ-систем.","Основные этапы разработки ИТ-систем.","Разбираться в этапах жизненного цикла.","Знает роли и виды декомпозиции.",draw.io,"materials/context.md","Рабочий кейс аналитика.",индивидуальный,,12,"4,08",8,120,75%,2,"Business analysis, Decomposition",,
"Заинтересованные стороны","Стейкхолдер
Каталог заинтересованных сторон",,BSA01_Stakeholders,"В этом проекте участник изучает, кто такие стейкхолдеры.","Основы выявления стейкхолдеров.","Определять и классифицировать стейкхолдеров.","Понимает значение стейкхолдеров.",Miro,"materials/stakeholders.md","Рабочая встреча с заказчиком.",групповой,3-4,16,"5,44",9,160,75%,2,"Stakeholder management, Communication",,
"""


class TestCurriculumUpload:
    """Тесты для endpoint /api/v1/curriculum/upload"""

    def test_upload_csv_success(self, client):
        """Успешная загрузка CSV файла."""
        csv_bytes = SAMPLE_CSV_CONTENT.encode('utf-8-sig')
        files = {"file": ("curriculum.csv", BytesIO(csv_bytes), "text/csv")}

        response = client.post("/api/v1/curriculum/upload", files=files)

        assert response.status_code == 200
        data = response.json()

        # Проверяем структуру ответа
        assert "direction" in data
        assert "direction_code" in data
        assert "blocks" in data
        assert len(data["blocks"]) == 2  # 2 блока в тестовых данных

        # Проверяем первый блок
        block1 = data["blocks"][0]
        assert "Блок 1" in block1["name"]
        assert len(block1["projects"]) == 2  # 2 проекта в первом блоке

        # Проверяем первый проект
        project1 = block1["projects"][0]
        assert project1["order"] == 1
        assert project1["title"] == "Проекты и процессы в управлении"
        assert project1["format"] == "individual"
        assert project1["platform_name"] == "PjM1_ProjPM"

        # Проверяем образовательные результаты
        assert len(project1["learning_outcomes"]) >= 1

    def test_upload_csv_with_group_format(self, client):
        """Проверка парсинга группового формата."""
        csv_bytes = SAMPLE_CSV_CONTENT.encode('utf-8-sig')
        files = {"file": ("curriculum.csv", BytesIO(csv_bytes), "text/csv")}

        response = client.post("/api/v1/curriculum/upload", files=files)

        assert response.status_code == 200
        data = response.json()

        # Находим групповой проект
        block2 = data["blocks"][1]  # Блок 2
        group_project = next(
            (p for p in block2["projects"] if p["format"] == "group"),
            None
        )

        assert group_project is not None
        assert group_project["group_size"] == 4  # "3-4" -> 4

    def test_upload_csv_direction_detection(self, client):
        """Проверка определения направления по названию проекта на платформе."""
        csv_bytes = SAMPLE_CSV_CONTENT.encode('utf-8-sig')
        files = {"file": ("curriculum.csv", BytesIO(csv_bytes), "text/csv")}

        response = client.post("/api/v1/curriculum/upload", files=files)

        assert response.status_code == 200
        data = response.json()

        # Направление должно быть определено как PjM (из PjM1_ProjPM)
        assert data["direction_code"] == "PjM"
        assert "Project Manager" in data["direction"]

    def test_upload_realistic_comma_csv_with_multiline_cells(self, client):
        """Парсит реальный шаблон УП: comma CSV, quoted multiline cells и колонку навыков."""
        csv_bytes = REALISTIC_COMMA_CSV_CONTENT.encode('utf-8-sig')
        files = {"file": ("project-manager-curriculum.csv", BytesIO(csv_bytes), "text/csv")}

        response = client.post("/api/v1/curriculum/upload", files=files)

        assert response.status_code == 200, response.text
        data = response.json()

        assert data["direction_code"] == "PjM"
        assert len(data["blocks"]) == 2

        first_block = data["blocks"][0]
        assert "Блок 1" in first_block["name"]
        assert len(first_block["projects"]) == 2

        first_project = first_block["projects"][0]
        assert first_project["order"] == 1
        assert first_project["title"] == "Проекты и процессы в управлении"
        assert first_project["format"] == "individual"
        assert first_project["skills"] == ["Project planning", "Analytical thinking"]
        assert first_project["audience_level"] == "Начальный"
        assert first_project["required_tools"] == ["Miro", "Google Docs"]
        assert "начинающий project manager" in first_project["sjm"]
        assert first_project["workload_days"] == 4.08

        group_project = data["blocks"][1]["projects"][0]
        assert group_project["format"] == "group"
        assert group_project["group_size"] == 4
        assert group_project["audience_level"] == "Средний"
        assert group_project["platform_name"] == "PjM5_ReqGather"

    def test_upload_program_template_with_content_unit_title_and_empty_order(self, client):
        """Парсит паспорт программы, где проект в колонке контентной единицы, а номер пустой."""
        csv_bytes = PROGRAM_TEMPLATE_COMMA_CSV_CONTENT.encode('utf-8-sig')
        files = {"file": ("program-template.csv", BytesIO(csv_bytes), "text/csv")}

        response = client.post("/api/v1/curriculum/upload", files=files)

        assert response.status_code == 200, response.text
        data = response.json()

        assert data["direction_code"] == "BSA"
        assert "Business Analytics" in data["direction"]
        assert len(data["blocks"]) == 2

        first_project = data["blocks"][0]["projects"][0]
        assert first_project["order"] == 1
        assert first_project["title"] == "BSA00_Decomposition"
        assert first_project["description"].startswith("В этом проекте участник изучает")
        assert first_project["required_software"] == "draw.io"
        assert first_project["additional_materials"] == "materials/context.md"
        assert first_project["skills"] == ["Business analysis", "Decomposition"]
        assert first_project["learning_outcomes"] == [
            "Основные этапы разработки ИТ-систем",
            "Разбираться в этапах жизненного цикла",
            "Знает роли и виды декомпозиции",
        ]

        second_project = data["blocks"][1]["projects"][0]
        assert second_project["order"] == 2
        assert second_project["format"] == "group"
        assert second_project["group_size"] == 4

    def test_upload_non_csv_file(self, client):
        """Ошибка при загрузке не-CSV файла."""
        files = {"file": ("document.txt", BytesIO(b"Hello World"), "text/plain")}

        response = client.post("/api/v1/curriculum/upload", files=files)

        assert response.status_code == 400
        assert "CSV" in response.json()["detail"]

    def test_upload_empty_csv(self, client):
        """Ошибка при загрузке пустого CSV."""
        empty_csv = "Тематический блок;Цели блока;№;Название проекта\n"
        files = {"file": ("empty.csv", BytesIO(empty_csv.encode('utf-8')), "text/csv")}

        response = client.post("/api/v1/curriculum/upload", files=files)

        assert response.status_code == 400

    def test_curriculum_context_building(self, client):
        """Проверка построения контекста для генерации."""
        csv_bytes = SAMPLE_CSV_CONTENT.encode('utf-8-sig')
        files = {"file": ("curriculum.csv", BytesIO(csv_bytes), "text/csv")}

        # Загружаем УП
        response = client.post("/api/v1/curriculum/upload", files=files)
        assert response.status_code == 200
        curriculum_data = response.json()

        # Строим контекст для второго проекта первого блока
        context_request = {
            "block_name": curriculum_data["blocks"][0]["name"],
            "project_order": 2,
            "curriculum_data": curriculum_data
        }

        response = client.post("/api/v1/curriculum/build-context", json=context_request)
        assert response.status_code == 200

        context = response.json()

        # Проверяем структуру контекста
        assert context["block_name"] == curriculum_data["blocks"][0]["name"]
        assert context["current_project_order"] == 2
        assert "current_project_description" in context
        assert "current_project_skills" in context
        assert "current_project_audience_level" in context
        assert "current_project_required_tools" in context

        # У второго проекта должен быть один предыдущий проект
        assert len(context["previous_projects"]) == 1
        assert context["previous_projects"][0]["order"] == 1

        # У второго проекта в первом блоке нет следующих проектов
        assert len(context["next_projects"]) == 0

        # Проверяем кросс-блочные связи (должны быть проекты из следующего блока)
        assert len(context["next_block_projects"]) >= 1


class TestCurriculumModels:
    """Тесты для моделей данных curriculum.py"""

    def test_curriculum_plan_build_context(self):
        """Тест построения контекста из CurriculumPlan."""
        from content_factory.generation.models.curriculum import (
            CurriculumPlan,
            CurriculumProject,
            ThematicBlock,
        )

        # Создаем тестовые данные
        projects_block1 = [
            CurriculumProject(
                block_name="Блок 1",
                block_goals=["Цель 1"],
                order=1,
                title="Проект 1",
                description="Описание 1",
                learning_outcomes=["LO1", "LO2"],
            ),
            CurriculumProject(
                block_name="Блок 1",
                block_goals=["Цель 1"],
                order=2,
                title="Проект 2",
                description="Описание 2",
                learning_outcomes=["LO3", "LO4"],
            ),
        ]

        projects_block2 = [
            CurriculumProject(
                block_name="Блок 2",
                block_goals=["Цель 2"],
                order=3,
                title="Проект 3",
                description="Описание 3",
                learning_outcomes=["LO5"],
            ),
        ]

        curriculum = CurriculumPlan(
            direction="Test Direction",
            direction_code="TST",
            blocks=[
                ThematicBlock(name="Блок 1", code="TST", goals=["Цель 1"], projects=projects_block1),
                ThematicBlock(name="Блок 2", code="TST", goals=["Цель 2"], projects=projects_block2),
            ]
        )

        # Строим контекст для второго проекта первого блока
        context = curriculum.build_context("Блок 1", 2)

        assert context is not None
        assert context.block_name == "Блок 1"
        assert context.current_project_order == 2
        assert len(context.previous_projects) == 1
        assert context.previous_projects[0].title == "Проект 1"
        assert len(context.next_projects) == 0

        # Проверяем кросс-блочные связи
        assert len(context.next_block_projects) == 1
        assert context.next_block_projects[0].title == "Проект 3"

    def test_curriculum_project_to_summary(self):
        """Тест конвертации проекта в краткую информацию."""
        from content_factory.generation.models.curriculum import CurriculumProject

        project = CurriculumProject(
            block_name="Блок 1",
            block_goals=["Цель"],
            order=1,
            title="Тестовый проект",
            description="Описание",
            learning_outcomes=["LO1", "LO2"],
            sjm="Тестовый кейс",
        )

        summary = project.to_summary()

        assert summary.order == 1
        assert summary.title == "Тестовый проект"
        assert summary.description == "Описание"
        assert summary.learning_outcomes == ["LO1", "LO2"]
        assert summary.block_name == "Блок 1"

    def test_thematic_block_get_all_learning_outcomes(self):
        """Тест получения всех LO блока."""
        from content_factory.generation.models.curriculum import CurriculumProject, ThematicBlock

        projects = [
            CurriculumProject(
                block_name="Блок",
                block_goals=[],
                order=1,
                title="Проект 1",
                learning_outcomes=["LO1", "LO2"],
            ),
            CurriculumProject(
                block_name="Блок",
                block_goals=[],
                order=2,
                title="Проект 2",
                learning_outcomes=["LO2", "LO3"],  # LO2 дублируется
            ),
        ]

        block = ThematicBlock(name="Блок", code="TST", goals=[], projects=projects)

        all_lo = block.get_all_learning_outcomes()

        # Дубликаты должны быть удалены
        assert len(all_lo) == 3
        assert "LO1" in all_lo
        assert "LO2" in all_lo
        assert "LO3" in all_lo
