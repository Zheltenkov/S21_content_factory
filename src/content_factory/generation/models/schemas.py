"""Pydantic схемы для контрактов данных."""

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, model_validator

from .enums import Language, ProjectType

# Избегаем циклического импорта
if TYPE_CHECKING:
    pass


StorytellingType = Literal["sjm", "case", "role_play", "project_scenario", "story_arc", "none"]


class ProjectSeed(BaseModel):
    """Входные данные от методолога для генерации проекта."""

    language: Language
    llm_provider: Literal["polza", "openrouter", "openai", "deepseek", "gigachat"] | None = Field(
        default=None,
        description="Предпочитаемый LLM provider для запуска: polza, openai, deepseek или gigachat",
    )
    project_type: ProjectType

    # Направление (было: thematic_block для BSA, Cb, DO, PjM, QA, DS)
    direction: str = Field(default="", description="Код направления (BSA, Cb, DO, PjM, QA, DS)")

    # Тематический блок из учебного плана (например, "Блок 1. Введение в проектную деятельность")
    thematic_block: str = Field(default="", description="Тематический блок из УП")

    audience_level: str = "base"
    required_tools: list[str] = Field(default_factory=list)
    required_software: list[str] | str | None = Field(
        default_factory=list,
        description="Необходимое ПО и среда выполнения отдельно от предметных инструментов",
    )
    project_content_type: str | None = Field(
        default=None,
        description="Явный профиль проекта: hard_code, low_code, no_code или auto",
    )
    title_seed: str = ""  # Название проекта
    project_description: str
    learning_outcomes: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    tasks_count: int | None = Field(default=None, ge=2, le=8)
    task_complexity: str | None = Field(
        default=None,
        description="Качественная сложность практических задач (easy/medium/hard)"
    )
    bonus_wish: str | None = None
    context_track_dir: str | None = Field(
        default=None,
        description="Путь к curriculum/context данным, если они переданы отдельно",
    )
    last_known_order: int | None = None
    group_size: int | None = Field(default=None, ge=2, le=10)  # Количество человек в группе (только для group)
    repo_base_url: str | None = None  # Базовый URL репозитория, если проект явно работает с Git/GitLab
    repo_path_template: str | None = None  # Канонический шаблон пути артефакта (например, ProjectName/part-03/task-{num:02d}/README.md)
    methodology_human_review: bool = Field(
        default=False,
        description="Включить human-in-the-loop паузы методолога между этапами генерации",
    )
    include_formulas: bool = Field(default=False, description="Разрешить формулы в теории")
    include_tables: bool = Field(default=False, description="Разрешить таблицы в теории")
    include_diagrams: bool = Field(default=False, description="Разрешить диаграммы в теории (mermaid)")
    is_programming_project: bool | None = Field(
        default=None,
        description="Является ли проект программистским (если None - определяется автоматически по навыкам)"
    )
    target_languages: list[str] | None = Field(
        default=None,
        description="Целевые языки программирования (если is_programming_project=True)"
    )
    zun: str | None = Field(
        default=None,
        description="Дополнительный учебный контекст для генерации (опционально)"
    )

    # === НОВЫЕ ПОЛЯ ИЗ УЧЕБНОГО ПЛАНА ===

    # Сторителлинг/моделирование среды (SJM)
    storytelling_type: StorytellingType = Field(
        default="sjm",
        description=(
            "Тип сторителлинга перед генерацией: sjm, case, role_play, "
            "project_scenario, story_arc или none"
        ),
    )
    sjm: str | None = Field(
        default=None,
        description="Сторителлинг/моделирование среды - кейс для погружения студента в проблему"
    )

    # Платформа и репозиторий из УП
    platform_name: str | None = Field(
        default=None,
        description="Название проекта на платформе и в GitLab"
    )
    gitlab_link: str | None = Field(
        default=None,
        description="Ссылки на GitLab/Google docs"
    )

    # Трудоемкость
    workload_hours: float | None = Field(
        default=None,
        description="Трудоемкость в астрономических часах"
    )
    workload_days: float | None = Field(
        default=None,
        description="Трудоемкость в днях"
    )

    # XP и порог прохождения
    xp_reward: int | None = Field(
        default=None,
        description="XP за проект"
    )

    # Дополнительные материалы
    additional_materials: str | None = Field(
        default=None,
        description="Дополнительные материалы для проекта"
    )

    # Подсказки для эксперта (что нужно разработать)
    expert_notes: str | None = Field(
        default=None,
        description="Что нужно разработать эксперту"
    )

    # Контекст из учебного плана (соседние проекты, цели блока и т.д.)
    curriculum_context: dict[str, Any] | None = Field(
        default=None,
        description="Контекст из УП: цели блока, соседние проекты, кросс-блочные связи"
    )
    pipeline_run_id: str | None = Field(
        default=None,
        description="Сквозной идентификатор pipeline-run от УП до генерации проекта",
    )
    source_plan_id: int | None = Field(
        default=None,
        description="ID исходного catalog.curriculum_plan, если генерация запущена из сохраненного УП",
    )
    plan_version: str | None = Field(
        default=None,
        description="Content-addressed версия frozen snapshot УП",
    )
    plan_hash: str | None = Field(
        default=None,
        description="SHA-256 hash frozen snapshot УП на момент подготовки генерации",
    )
    plan_row_id: int | None = Field(
        default=None,
        description="ID строки catalog.curriculum_plan_row для генерируемого проекта",
    )
    project_index: int | None = Field(
        default=None,
        description="Стабильный индекс проекта внутри блока исходного УП",
    )
    curriculum_origin: dict[str, Any] | None = Field(
        default=None,
        description="Полная lineage-запись: plan_id/version/hash/row_id/project_index/pipeline_run_id",
    )

    # Общий эталонный фрагмент/идеальный образ проекта для ориентира по структуре и стилю
    reference_project_hint: str | None = Field(
        default=None,
        description="Эталонный фрагмент идеального проекта — используется как ориентир по структуре и стилю без общего retrieval"
    )

    # Режим «приближение к референсу»: фрагмент эталонного README (Глава 3 / задания) для ориентации по структуре и стилю
    reference_practice_hint: str | None = Field(
        default=None,
        description="Эталонный фрагмент заданий (из существующего README) — при генерации практики ориентироваться на его структуру и стиль"
    )

    @model_validator(mode='before')
    @classmethod
    def normalize_seed_inputs(cls, data: Any) -> Any:
        """Приводит UI/legacy payload к стабильному seed-контракту."""
        if not isinstance(data, dict):
            return data

        def to_list(value: Any) -> list[str]:
            if value is None:
                return []
            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()]
            return [item.strip() for item in str(value).split(",") if item.strip()]

        if "required_software" in data:
            data["required_software"] = to_list(data.get("required_software"))
        if "required_tools" in data:
            data["required_tools"] = to_list(data.get("required_tools"))
        if "storytelling_type" in data:
            data["storytelling_type"] = cls._normalize_storytelling_type(data.get("storytelling_type"))
        return data

    @model_validator(mode='after')
    def normalize_lists(self):
        """Нормализация данных после инициализации."""
        self.skills = list(dict.fromkeys(s.strip() for s in self.skills if s.strip()))
        self.learning_outcomes = list(
            dict.fromkeys(lo.strip() for lo in self.learning_outcomes if lo.strip())
        )
        self.required_tools = list(
            dict.fromkeys(tool.strip() for tool in self.required_tools if tool.strip())
        )
        required_software = self.required_software or []
        if isinstance(required_software, str):
            required_software = [item.strip() for item in required_software.split(",") if item.strip()]
        self.required_software = list(
            dict.fromkeys(str(item).strip() for item in required_software if str(item).strip())
        )
        self.audience_level = self._normalize_audience_level(self.audience_level)
        self.project_content_type = self._normalize_project_content_type(self.project_content_type)
        self.storytelling_type = self._normalize_storytelling_type(self.storytelling_type)
        # Обратная совместимость: если direction пустой, используем thematic_block как direction
        if not self.direction and self.thematic_block:
            # Проверяем, похоже ли thematic_block на код направления (BSA, Cb, etc.)
            known_directions = {'BSA', 'Cb', 'DO', 'PjM', 'QA', 'DS'}
            if self.thematic_block in known_directions:
                self.direction = self.thematic_block
                self.thematic_block = ""

        # УНИВЕРСАЛЬНАЯ ПРАВКА: Автогенерация repo_path_template из platform_name
        # Это уберёт "repo/..." из всех проектов и сделает пути консистентными
        normalized_repo_template = (self.repo_path_template or "").replace("\\", "/").strip().lstrip("./").lower()
        if self.platform_name and (not self.repo_path_template or normalized_repo_template.startswith("repo/")):
            # Автоматически создаём путь на основе platform_name
            self.repo_path_template = f"{self.platform_name}/part-03/task-{{num:02d}}/README.md"

        return self

    @staticmethod
    def _normalize_audience_level(value: str | None) -> str:
        norm = (value or "").strip().lower()
        if norm in {"beginner_plus", "beginner+", "basic+", "base+", "базовый+", "начальный+"}:
            return "beginner_plus"
        if norm in {"beginner", "basic", "base", "базовый", "начальный"}:
            return "beginner"
        if norm in {"middle", "intermediate", "средний"}:
            return "middle"
        if norm in {"advanced", "продвинутый"}:
            return "advanced"
        if norm in {"professional", "pro", "expert", "профессиональный", "экспертный"}:
            return "professional"
        return "beginner_plus"

    @staticmethod
    def _normalize_project_content_type(value: str | None) -> str | None:
        norm = (value or "").strip().lower()
        if norm in {"", "auto"}:
            return None
        aliases = {
            "technical_code": "hard_code",
            "programming": "hard_code",
            "code": "hard_code",
            "technical": "low_code",
            "technical_low_code": "low_code",
            "analytical": "low_code",
            "design_product": "no_code",
            "humanitarian": "no_code",
            "management": "no_code",
            "business": "no_code",
        }
        normalized = aliases.get(norm, norm)
        return normalized if normalized in {"hard_code", "low_code", "no_code"} else None

    @staticmethod
    def _normalize_storytelling_type(value: Any) -> StorytellingType:
        norm = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "": "sjm",
            "storytelling": "sjm",
            "story": "story_arc",
            "сюжет": "story_arc",
            "сквозная_история": "story_arc",
            "кейс": "case",
            "рабочий_кейс": "case",
            "role": "role_play",
            "roleplay": "role_play",
            "role_play": "role_play",
            "ролевая_ситуация": "role_play",
            "scenario": "project_scenario",
            "project_story": "project_scenario",
            "project_scenario": "project_scenario",
            "сценарий_проекта": "project_scenario",
            "none": "none",
            "нет": "none",
        }
        normalized = aliases.get(norm, norm)
        if normalized in {"sjm", "case", "role_play", "project_scenario", "story_arc", "none"}:
            return normalized  # type: ignore[return-value]
        return "sjm"


class ProjectContextMeta(BaseModel):
    """Метаданные curriculum/context слоя для выравнивания генерации."""

    track: str
    thematic_block: str  # Тематический блок (публичное поле)
    last_order: int = 0
    aligned_skills: list[str] = Field(default_factory=list)
    narrative_anchor: str = ""
    similar_projects: list[dict[str, Any]] = Field(default_factory=list)
    search_metrics: dict[str, Any] = Field(default_factory=dict)  # Legacy key: kept for report consumers
    context_summary: str = ""  # Резюме контекста из curriculum/context-analysis слоя
    context_profiles_used: dict[str, Any] = Field(default_factory=dict)
    context_levels: list[dict[str, Any]] = Field(default_factory=list)


class Annotation(BaseModel):
    """Аннотация проекта."""

    text: str
    chars: int


class IntroSection(BaseModel):
    """Глава 1: Введение и инструкция."""

    intro_text: str
    instruction_text: str


class TheoryPart(BaseModel):
    """Часть теоретического раздела."""

    title: str
    body: str
    example: str
    bridge_questions: list[str] = Field(default_factory=list)
    covers_outcomes: list[str] = Field(default_factory=list)
    references: list[dict[str, str]] = Field(default_factory=list)  # [{"url": "...", "title": "...", "description": "..."}]
    # Новые поля для разделения текста и улучшений
    text_markdown: str | None = Field(default=None, description="Исходный Markdown текст (без улучшений)")
    enhancement_anchors: dict[str, str] | None = Field(
        default=None,
        description="Якоря для встраивания: {'formula_accuracy': '{{INSERT_FORMULA:accuracy}}', 'diagram_lifecycle': '{{INSERT_DIAGRAM:project_lifecycle}}'}"
    )


class PracticeTask(BaseModel):
    """Практическая задача."""

    title: str
    situation: str = Field(default="", description="Краткий рабочий контекст задачи: ситуация, проблема, ограничение")
    constraints_or_risk: str = Field(default="", description="Явное ограничение, риск или точка выбора в задаче")
    input_data: str = ""
    goal: str
    approach_bullets: list[str] = Field(default_factory=list)
    expected_artifact: str
    artifact_location: str = ""
    p2p_checkable: bool = True
    p2p_criteria: list[str] = Field(default_factory=list, description="Критерии P2P-проверки (чеклист для ревьюера)")
    covered_outcomes: list[str] = Field(default_factory=list, description="Какие LO покрывает задача")
    theory_support: list[str] = Field(default_factory=list, description="Какие темы/понятия из теории поддерживают задачу")
    group_roles: list[str] | None = None


class ProjectSpec(BaseModel):
    """Полная спецификация сгенерированного проекта."""

    language: Language
    project_type: ProjectType
    thematic_block: str  # Было: track
    required_tools: list[str] = Field(default_factory=list)
    title: str
    annotation: Annotation
    toc_md: str | None = None
    intro: IntroSection
    theory: list[TheoryPart] = Field(default_factory=list)
    practice: list[PracticeTask] = Field(default_factory=list)
    checklist_yml: str | None = Field(
        default=None,
        description="YAML-чек-лист для p2p-проверки, сформированный из финального README",
    )
    bonus: str | None = None
    context: ProjectContextMeta

