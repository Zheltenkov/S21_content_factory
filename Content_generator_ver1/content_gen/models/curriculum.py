"""
content_gen/models/curriculum.py

Модели данных для работы с учебным планом (УП / Паспорт программы).

Поддерживает:
- Парсинг CSV файла УП
- Каскадные селекторы: Направление → Тематический блок → Проект
- Контекст для генерации с учетом соседних проектов (внутри блока и кросс-блочные)
"""

from typing import Any

from pydantic import BaseModel, Field


class ProjectSummary(BaseModel):
    """Краткая информация о проекте для контекста генерации."""

    order: int = Field(..., description="Номер проекта в блоке")
    title: str = Field(..., description="Название проекта")
    description: str = Field(default="", description="Краткое описание")
    learning_outcomes: list[str] = Field(default_factory=list, description="Образовательные результаты")
    block_name: str | None = Field(default=None, description="Название блока (для кросс-блочных связей)")


class CurriculumContext(BaseModel):
    """
    Контекст из учебного плана для генерации контента.
    
    Передается в агенты для понимания места проекта в курсе.
    """

    # Информация о текущем блоке
    block_name: str = Field(..., description="Название тематического блока")
    block_goals: list[str] = Field(default_factory=list, description="Цели всего блока")
    current_project_order: int = Field(..., description="Номер текущего проекта в блоке")
    current_project_description: str = Field(default="", description="Краткое описание текущего проекта")
    current_project_skills: list[str] = Field(default_factory=list, description="Список навыков текущего проекта")
    current_project_audience_level: str | None = Field(default=None, description="Уровень аудитории текущего проекта")
    current_project_required_tools: list[str] = Field(default_factory=list, description="Обязательные инструменты текущего проекта")
    current_project_required_software: str | None = Field(default=None, description="Необходимое ПО текущего проекта")

    # Соседние проекты внутри блока (приоритет)
    previous_projects: list[ProjectSummary] = Field(
        default_factory=list,
        description="Предыдущие проекты в блоке (что студент уже изучил)"
    )
    next_projects: list[ProjectSummary] = Field(
        default_factory=list,
        description="Следующие проекты в блоке (что студент будет изучать)"
    )

    # Все LO блока для понимания траектории
    all_block_learning_outcomes: list[str] = Field(
        default_factory=list,
        description="Все образовательные результаты блока"
    )

    # Кросс-блочные связи
    previous_block_projects: list[ProjectSummary] = Field(
        default_factory=list,
        description="Последние проекты предыдущего блока (для кросс-блочных связей)"
    )
    next_block_projects: list[ProjectSummary] = Field(
        default_factory=list,
        description="Первые проекты следующего блока (для кросс-блочных связей)"
    )

    # SJM - сторителлинг/кейс
    storytelling_type: str = Field(
        default="sjm",
        description="Тип сторителлинга: sjm, case, role_play, project_scenario, story_arc или none",
    )
    sjm_context: str | None = Field(
        default=None,
        description="Сторителлинг/моделирование среды - кейс для погружения студента"
    )

    # Подсказки для эксперта
    expert_development_notes: str | None = Field(
        default=None,
        description="Что нужно разработать эксперту"
    )

    # Дополнительные материалы
    additional_materials: str | None = Field(
        default=None,
        description="Дополнительные материалы для проекта"
    )


class CurriculumProject(BaseModel):
    """Полная информация о проекте из учебного плана."""

    # Идентификация
    block_name: str = Field(..., description="Название тематического блока")
    block_goals: list[str] = Field(default_factory=list, description="Цели блока")
    order: int = Field(..., description="Номер проекта в блоке")

    # Основная информация
    title: str = Field(..., description="Название проекта")
    description: str = Field(default="", description="Краткое описание проекта")
    learning_outcomes: list[str] = Field(default_factory=list, description="Образовательные результаты")
    skills: list[str] = Field(default_factory=list, description="Список навыков")
    audience_level: str | None = Field(default=None, description="Уровень аудитории")
    required_tools: list[str] = Field(default_factory=list, description="Обязательные инструменты")

    # Формат
    format: str = Field(default="individual", description="individual или group")
    group_size: int | None = Field(default=None, description="Количество человек в группе")

    # Техническая информация
    required_software: str | None = Field(default=None, description="Необходимое ПО/веб")

    # Трудоемкость
    workload_hours: float | None = Field(default=None, description="Трудоемкость в астрономических часах")
    workload_days: float | None = Field(default=None, description="Трудоемкость в днях")
    total_workload_days: float | None = Field(default=None, description="Общая трудоемкость в днях")
    xp: int | None = Field(default=None, description="XP за проект")
    passing_threshold: str | None = Field(default=None, description="Процент прохождения проекта")

    # Контент
    storytelling_type: str | None = Field(default=None, description="Тип сторителлинга")
    sjm: str | None = Field(default=None, description="Сторителлинг/моделирование среды")
    expert_notes: str | None = Field(default=None, description="Что нужно разработать эксперту")
    additional_materials: str | None = Field(default=None, description="Дополнительные материалы")

    # Платформа и репозиторий
    platform_name: str | None = Field(default=None, description="Название проекта на платформе и в GitLab")
    gitlab_link: str | None = Field(default=None, description="Ссылки на GitLab/Google docs")

    def to_summary(self) -> ProjectSummary:
        """Конвертирует в краткую информацию для контекста."""
        return ProjectSummary(
            order=self.order,
            title=self.title,
            description=self.description,
            learning_outcomes=self.learning_outcomes,
            block_name=self.block_name
        )


class ThematicBlock(BaseModel):
    """Тематический блок учебного плана."""

    name: str = Field(..., description="Полное название блока")
    code: str = Field(default="UNK", description="Код блока (PjM, BSA и т.д.)")
    goals: list[str] = Field(default_factory=list, description="Цели блока")
    projects: list[CurriculumProject] = Field(default_factory=list, description="Проекты в блоке")

    def get_all_learning_outcomes(self) -> list[str]:
        """Возвращает все образовательные результаты блока."""
        outcomes = []
        for project in self.projects:
            outcomes.extend(project.learning_outcomes)
        return list(dict.fromkeys(outcomes))  # Уникальные с сохранением порядка


class CurriculumPlan(BaseModel):
    """Полный учебный план (паспорт программы)."""

    direction: str = Field(default="Unknown", description="Направление (Project Manager, BSA и т.д.)")
    direction_code: str = Field(default="UNK", description="Код направления")
    blocks: list[ThematicBlock] = Field(default_factory=list, description="Тематические блоки")

    def get_block_by_name(self, name: str) -> ThematicBlock | None:
        """Находит блок по названию."""
        for block in self.blocks:
            if block.name == name:
                return block
        return None

    def get_project(self, block_name: str, project_order: int) -> CurriculumProject | None:
        """Находит проект по блоку и номеру."""
        block = self.get_block_by_name(block_name)
        if not block:
            return None
        for project in block.projects:
            if project.order == project_order:
                return project
        return None

    def build_context(self, block_name: str, project_order: int, cross_block_depth: int = 2) -> CurriculumContext | None:
        """
        Строит контекст для генерации.
        
        Args:
            block_name: Название блока
            project_order: Номер проекта
            cross_block_depth: Сколько проектов из соседних блоков включать
            
        Returns:
            CurriculumContext или None если проект не найден
        """
        block = self.get_block_by_name(block_name)
        if not block:
            return None

        project = None
        project_index = -1
        for i, p in enumerate(block.projects):
            if p.order == project_order:
                project = p
                project_index = i
                break

        if not project:
            return None

        # Проекты внутри блока
        previous_projects = [p.to_summary() for p in block.projects[:project_index]]
        next_projects = [p.to_summary() for p in block.projects[project_index + 1:]]

        # Кросс-блочные связи
        block_index = self.blocks.index(block)
        previous_block_projects = []
        next_block_projects = []

        if block_index > 0:
            prev_block = self.blocks[block_index - 1]
            # Последние N проектов предыдущего блока
            previous_block_projects = [
                p.to_summary() for p in prev_block.projects[-cross_block_depth:]
            ]

        if block_index < len(self.blocks) - 1:
            next_block = self.blocks[block_index + 1]
            # Первые N проектов следующего блока
            next_block_projects = [
                p.to_summary() for p in next_block.projects[:cross_block_depth]
            ]

        return CurriculumContext(
            block_name=block.name,
            block_goals=block.goals,
            current_project_order=project_order,
            current_project_description=project.description or "",
            current_project_skills=project.skills or [],
            current_project_audience_level=project.audience_level,
            current_project_required_tools=project.required_tools or [],
            previous_projects=previous_projects,
            next_projects=next_projects,
            all_block_learning_outcomes=block.get_all_learning_outcomes(),
            previous_block_projects=previous_block_projects,
            next_block_projects=next_block_projects,
            storytelling_type=project.storytelling_type or "sjm",
            sjm_context=project.sjm,
            expert_development_notes=project.expert_notes,
            additional_materials=project.additional_materials
        )

    def to_dict_for_frontend(self) -> dict[str, Any]:
        """Сериализует для передачи на фронтенд."""
        return {
            "direction": self.direction,
            "direction_code": self.direction_code,
            "blocks": [
                {
                    "name": block.name,
                    "code": block.code,
                    "goals": block.goals,
                    "projects": [
                        {
                            "order": p.order,
                            "title": p.title,
                            "description": p.description,
                            "learning_outcomes": p.learning_outcomes,
                            "skills": p.skills,
                            "audience_level": p.audience_level,
                            "required_tools": p.required_tools,
                            "format": p.format,
                            "group_size": p.group_size,
                            "required_software": p.required_software,
                            "workload_hours": p.workload_hours,
                            "workload_days": p.workload_days,
                            "xp": p.xp,
                            "storytelling_type": p.storytelling_type,
                            "sjm": p.sjm,
                            "expert_notes": p.expert_notes,
                            "additional_materials": p.additional_materials,
                            "platform_name": p.platform_name,
                            "gitlab_link": p.gitlab_link,
                        }
                        for p in block.projects
                    ]
                }
                for block in self.blocks
            ]
        }
