"""Column names of the plans the module reads: the existing generator's CSV, earlier exports, and the standard
programme passport (workbook «Паспорт программы», tab «УП»; checked against «Паспорт программы_Батон» 05.10.2026).

Kept local so the prototype does not import the production API, auth or database.
Unmapped columns are still preserved in CurriculumRow.raw as source evidence.
"""

COLUMNS: dict[str, tuple[str, ...]] = {
    "program": ("Программа", "Название программы"),
    "block": ("Тематический блок", "Название всего блока (если делим на блоки)", "Название блока"),
    "goals": ("Цели блока",),
    "order": ("№", "Код модуля"),
    # The project's name on the platform and in GitLab (D1P1_Terminal): the key that ties a plan row to its block
    # on the skills tab.
    "code": ("Название проекта на платформе и в Gitlab", "Код проекта"),
    # Where the project lives or will live: the address of its repository. Not read by the concept; the content stage
    # checks by it that the files of the project are named by their paths from the root of the repository.
    "repository": ("GitLab", "Ссылка на GitLab", "GitLab (ссылка)", "Репозиторий", "Репозиторий проекта", "Репозиторий GitLab",
                   "Репозиторий в GitLab", "GitLab-репозиторий", "Ссылка на репозиторий", "Ссылка на проект в GitLab"),
    "title": ("Название проекта", "Название контентной единицы", "Название контентной единицы (проекта)", "Модуль", "Название модуля"),
    # The passport has no «краткое описание» (that is marketing copy of the programme): what the project consists of
    # is in its theses column.
    "description": ("Краткое описание проекта", "Краткое описание", "Тезисное тематическое наполнение проекта"),
    # The approved content of the project as a numbered list of cases.
    "cases": ("Кейсы, которые будут решаться в проекте", "Кейсы проекта"),
    "outcomes": (
        "Образовательные результаты (знает, понимает, умеет)", "Образовательные результаты",
        "Обр. результаты — что узнает (ЗНАТЬ)", "Обр. результаты — что умеет (УМЕТЬ)",
        "Обр. результаты — какой навык (НАВЫКИ)",
    ),
    "skills": ("Список навыков", "Список навыков (развесовка)", "Навыки"),
    "audience": ("Уровень аудитории",),
    "tools": ("Обязательные инструменты (через запятую)", "Обязательные инструменты", "Инструменты"),
    "software": ("Необходимое ПО и среда", "Необходимое ПО/веб", "Необходимое ПО"),
    "storytelling": (
        "Сторителлинг", "Сторителтнг", "SJM",
        "SJM (описание ситуации/кейса, с которым сталкивается участник, сторителлинг или моделирование среды)",
    ),
    "storytelling_type": ("Тип сторителлинга", "storytelling type", "storytelling_type"),
    "format": ("Формат",),
    "group_size": ("Кол-во в группе",),
    "hours": ("Трудоемкость, астр.часы", "Трудоёмкость, астр. часы", "Часы", "Трудоёмкость, ч"),
    # Read only when the plan has no astronomical hours: the module counts in astronomical ones (ingestion.hours_from).
    "hours_academic": ("Трудоемкость, ак.часы", "Нагрузка в ак. часах"),
    "checking": ("Форма контроля", "Форма проверки", "Формат проверки"),
    "materials": ("Дополнительные материалы", "Дополнительные материалы для генерации", "Доп. материалы для генерации"),
}
