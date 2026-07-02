from content_gen.models.schemas import PracticeTask
from content_gen.validators.practice_checks import PracticeChecks


def test_practice_checks_use_correct_criterion_ids_and_accept_imperative_goals():
    task = PracticeTask(
        title="Схема взаимодействия",
        situation="Команда спорит, где должна жить бизнес-логика, и из-за этого frontend и backend по-разному трактуют один и тот же сценарий. Если не зафиксировать схему сейчас, задача уйдёт в разработку с разными ожиданиями.",
        constraints_or_risk="Если пропустить критические точки обмена данными, команда примет разные допущения и сломает сценарий на интеграции.",
        input_data="Описание интерфейсов — см. файл `materials/api_context.md`",
        goal="Определи точки взаимодействия frontend и backend и подготовь схему.",
        approach_bullets=[
            "Выдели роли участников и точки обмена данными.",
            "Собери схему взаимодействия и проверь её на полноту.",
        ],
        expected_artifact="Схема взаимодействия размещена в `PjM20_FrontBack/part-03/task-03/interaction_diagram.png`",
        artifact_location="PjM20_FrontBack/part-03/task-03/interaction_diagram.png",
        p2p_criteria=[
            "На схеме указаны роли frontend и backend.",
            "Отмечены минимум 3 точки обмена данными.",
            "Файл размещён по указанному пути.",
        ],
        covered_outcomes=["Определить точки взаимодействия frontend и backend"],
        theory_support=["Типы взаимодействия компонентов"],
    )

    result = PracticeChecks(language="ru", expected_tasks=1).check([task])

    assert not any(issue.criterion_id == "2.5.3" and issue.severity == "hard" for issue in result.all_issues)
    assert not any(issue.criterion_id == "2.5.6" and issue.severity == "hard" for issue in result.all_issues)
    assert not any(issue.criterion_id == "2.5.4" for issue in result.all_issues)


def test_practice_checks_accept_domain_active_goal_verbs():
    task = PracticeTask(
        title="Идентификация рисков",
        situation="Команда получила заметки со встречи и должна быстро подготовить основу для обсуждения рисков с заказчиком.",
        constraints_or_risk="Если риски не классифицировать до встречи, команда не сможет выбрать реалистичные меры реагирования.",
        input_data="Сырые заметки встречи — см. файл `materials/team_notes.md`",
        goal="Идентифицировать и классифицировать проектные риски на основе собранной информации.",
        approach_bullets=[
            "Выдели признаки риска из заметок встречи.",
            "Сопоставь риски с категориями и последствиями.",
        ],
        expected_artifact="Таблица рисков размещена в `PjM8_RisksMng/part-03/task-01/risk_table.md`",
        artifact_location="PjM8_RisksMng/part-03/task-01/risk_table.md",
        p2p_criteria=[
            "Таблица содержит минимум 5 рисков.",
            "Каждый риск классифицирован по категории.",
            "Файл размещён по указанному пути.",
        ],
        covered_outcomes=["Определять и формулировать проектные риски"],
        theory_support=["Классификация рисков"],
    )

    result = PracticeChecks(language="ru", expected_tasks=1).check([task])

    assert not any(issue.criterion_id == "2.5.3" for issue in result.all_issues)


def test_practice_checks_report_missing_p2p_and_keep_approach_under_254():
    task = PracticeTask(
        title="Инструкция для тестирования API",
        situation="Тестировщик получил обновлённый API, но не понимает, какие ответы считать корректными после изменения логики. Если не оформить инструкцию, проверка будет зависеть от догадок каждого ревьюера.",
        constraints_or_risk="Если не зафиксировать ожидаемые ответы, разные участники будут по-разному принимать один и тот же результат.",
        input_data="Файл с требованиями к API — см. файл `materials/api_requirements.md`",
        goal="Создай инструкцию для проверки API.",
        approach_bullets=[
            "Опиши последовательность проверки.",
            "Укажи наблюдаемые признаки корректной работы.",
        ],
        expected_artifact="Инструкция размещена в `PjM20_FrontBack/part-03/task-02/testing_instructions.md`",
        artifact_location="PjM20_FrontBack/part-03/task-02/testing_instructions.md",
        p2p_criteria=[],
        covered_outcomes=["Подготовить инструкцию проверки API"],
        theory_support=["Сценарии тестирования API"],
    )

    result = PracticeChecks(language="ru", expected_tasks=1).check([task])

    assert any(issue.criterion_id == "2.5.6" and issue.severity == "hard" for issue in result.all_issues)
    assert any(issue.message.startswith("Задание 1 'Инструкция для тестирования API'") for issue in result.all_issues)
    assert not any(issue.criterion_id == "2.5.3" and "подход содержит" in issue.message for issue in result.all_issues)


def test_practice_checks_require_story_driven_situation_block():
    task = PracticeTask(
        title="Сбор требований",
        situation="",
        input_data="Описание запроса заказчика — см. файл `materials/customer_request.md`",
        goal="Сформируй список уточняющих вопросов к требованиям.",
        approach_bullets=[
            "Выдели неясные места в описании запроса.",
            "Собери вопросы так, чтобы команда могла снять риски до старта работ.",
        ],
        expected_artifact="Список вопросов размещён в `PjM20_FrontBack/part-03/task-01/questions.md`",
        artifact_location="PjM20_FrontBack/part-03/task-01/questions.md",
        p2p_criteria=[
            "Есть минимум 5 уточняющих вопросов.",
            "Вопросы связаны с ролями frontend и backend.",
            "Файл размещён по указанному пути.",
        ],
        covered_outcomes=["Собрать уточняющие вопросы"],
        theory_support=["Анализ требований"],
    )

    result = PracticeChecks(language="ru", expected_tasks=1).check([task])

    assert any(issue.criterion_id == "2.5.2" and issue.severity == "hard" for issue in result.all_issues)


def test_practice_checks_warn_when_risk_and_lo_mapping_are_missing():
    task = PracticeTask(
        title="Выбор хостинга",
        situation="Команда выбирает, где разместить сервис, и боится ошибиться с нагрузкой.",
        constraints_or_risk="",
        input_data="Описание нагрузок — см. файл `materials/load_profile.md`",
        goal="Выбери подходящий тип хостинга и опиши решение.",
        approach_bullets=[
            "Сопоставь требования проекта с вариантами размещения.",
            "Зафиксируй аргументы выбора.",
        ],
        expected_artifact="Документ размещён в `PjM21_ITInfStruct/part-03/task-02/README.md`",
        artifact_location="PjM21_ITInfStruct/part-03/task-02/README.md",
        p2p_criteria=[
            "Указан тип хостинга.",
            "Описаны аргументы выбора.",
            "Файл размещён по указанному пути.",
        ],
    )

    result = PracticeChecks(language="ru", expected_tasks=1).check([task])

    assert any(issue.criterion_id == "2.5.2" and issue.severity == "soft" for issue in result.all_issues)
    assert any(issue.criterion_id == "2.5.7" for issue in result.all_issues)


def test_practice_checks_accept_observable_generator_phrases_in_p2p():
    task = PracticeTask(
        title="Смета затрат",
        situation="Команда готовит смету проекта для защиты перед ревьюером и должна показать её в понятном проверяемом виде.",
        constraints_or_risk="Если не учесть ключевые статьи расходов, защита сметы сорвётся.",
        input_data="Черновая таблица затрат — см. файл `materials/budget.xlsx`",
        goal="Подготовь итоговую смету расходов проекта.",
        approach_bullets=[
            "Сверь категории затрат с материалами проекта.",
            "Зафиксируй расчёты и выводы по смете.",
        ],
        expected_artifact="Итоговая таблица размещена в `PjM12_Budget/part-03/task-02/budget.xlsx`",
        artifact_location="PjM12_Budget/part-03/task-02/budget.xlsx",
        p2p_criteria=[
            "В таблице перечислены все категории затрат.",
            "Учтены человеко-часы и платные сервисы.",
            "Файл размещён по указанному пути.",
        ],
        covered_outcomes=["Подготовить итоговую смету"],
        theory_support=["Человеко-часы и их значение"],
    )

    result = PracticeChecks(language="ru", expected_tasks=1).check([task])

    assert not any(issue.criterion_id == "2.5.6" for issue in result.all_issues)


def test_practice_checks_reject_solution_like_materials():
    task = PracticeTask(
        title="SWOT-анализ",
        situation="Команда уже собрала первичные сведения о рисках и должна сама классифицировать их для ревью.",
        constraints_or_risk="Если взять готовую матрицу, ревьюер не увидит самостоятельную работу по классификации.",
        input_data="Готовый SWOT-анализ — см. файл `materials/swot_analysis.md`",
        goal="Классифицируй риски через SWOT.",
        approach_bullets=[
            "Сопоставь риски с категориями SWOT.",
            "Зафиксируй аргументы классификации.",
        ],
        expected_artifact="SWOT-матрица размещена в `PjM8_RisksMng/part-03/task-02/swot_analysis.md`",
        artifact_location="PjM8_RisksMng/part-03/task-02/swot_analysis.md",
        p2p_criteria=[
            "SWOT-матрица содержит все 4 категории.",
            "Для каждой категории есть аргументация.",
            "Файл размещён по указанному пути.",
        ],
        covered_outcomes=["Классифицировать риски по категориям"],
        theory_support=["SWOT-анализ"],
    )

    result = PracticeChecks(language="ru", expected_tasks=1).check([task])

    assert any(issue.criterion_id == "2.5.materials" for issue in result.all_issues)


def test_practice_checks_reject_processed_material_phrases_even_with_neutral_filename():
    task = PracticeTask(
        title="Реестр рисков",
        situation="Команда получила первичные сведения и должна сама выделить риски перед встречей с заказчиком.",
        constraints_or_risk="Если студент получит готовый реестр, учебная деятельность сведётся к переносу ответа.",
        input_data="Готовый реестр рисков с классификацией — см. файл `materials/task_01_source_notes.md`",
        goal="Идентифицируй проектные риски.",
        approach_bullets=["Выдели риски из исходных сведений.", "Сопоставь риски с категориями."],
        expected_artifact="Реестр рисков размещён в `PjM8_RisksMng/part-03/task-01/risk_register.md`",
        artifact_location="PjM8_RisksMng/part-03/task-01/risk_register.md",
        p2p_criteria=[
            "Реестр содержит минимум 5 рисков.",
            "Для каждого риска указана категория.",
            "Файл размещён по указанному пути.",
        ],
        covered_outcomes=["Идентифицировать риски"],
        theory_support=["Виды рисков"],
    )

    result = PracticeChecks(language="ru", expected_tasks=1).check([task])

    assert any(issue.criterion_id == "2.5.materials" for issue in result.all_issues)


def test_practice_checks_require_dependency_on_previous_task_artifact():
    first = PracticeTask(
        title="Идентификация рисков",
        situation="Команда собирает сведения о проблемах прошлых проектов перед планированием нового запуска.",
        constraints_or_risk="Если пропустить риск, план реагирования не покроет критичную угрозу.",
        input_data="Сырые заметки по инцидентам — см. файл `materials/task_01_source_notes.md`",
        goal="Определи ключевые проектные риски.",
        approach_bullets=["Выдели риски из заметок.", "Зафиксируй вероятность и влияние."],
        expected_artifact="Таблица рисков размещена в `PjM8_RisksMng/part-03/task-01/risk_table.md`",
        artifact_location="PjM8_RisksMng/part-03/task-01/risk_table.md",
        p2p_criteria=[
            "Таблица содержит минимум 5 рисков.",
            "Указаны вероятность и влияние.",
            "Файл размещён по указанному пути.",
        ],
        covered_outcomes=["Определять и формулировать проектные риски"],
        theory_support=["Виды проектных рисков"],
    )
    second = PracticeTask(
        title="Классификация рисков",
        situation="После первичного сбора команда должна разложить риски по категориям, чтобы выбрать фокус обсуждения.",
        constraints_or_risk="Если классификация не опирается на предыдущий список, задачи проекта распадаются на несвязанные упражнения.",
        input_data="Описание метода SWOT — см. файл `materials/task_02_source_notes.md`",
        goal="Классифицируй риски через SWOT.",
        approach_bullets=["Сопоставь риски с категориями SWOT.", "Подготовь вывод по классификации."],
        expected_artifact="SWOT-матрица размещена в `PjM8_RisksMng/part-03/task-02/swot_analysis.md`",
        artifact_location="PjM8_RisksMng/part-03/task-02/swot_analysis.md",
        p2p_criteria=[
            "SWOT-матрица содержит все 4 категории.",
            "Каждый риск отнесён к категории.",
            "Файл размещён по указанному пути.",
        ],
        covered_outcomes=["Классифицировать риски по категориям"],
        theory_support=["SWOT-анализ"],
    )

    result = PracticeChecks(language="ru", expected_tasks=2).check([first, second])

    assert any(issue.criterion_id == "2.5.dependency" for issue in result.all_issues)

    second.input_data += "\nРезультат предыдущей задачи — см. файл `PjM8_RisksMng/part-03/task-01/risk_table.md`."
    result = PracticeChecks(language="ru", expected_tasks=2).check([first, second])

    assert not any(issue.criterion_id == "2.5.dependency" for issue in result.all_issues)


def test_practice_checks_can_validate_single_bonus_task_without_count_rule():
    task = PracticeTask(
        title="Расширенная защита решения",
        situation="Заказчик просит показать, почему выбранное решение выдержит ограничения проекта.",
        constraints_or_risk="Если аргументы останутся общими, ревьюер не сможет проверить готовность решения.",
        input_data="Сырые заметки — см. файл `materials/task_01_source_notes.md`",
        goal="Сформировать расширенную защиту решения для заказчика.",
        approach_bullets=[
            "Сопоставь решение с ограничениями проекта.",
            "Зафиксируй аргументы и проверяемые выводы.",
        ],
        expected_artifact="Документ размещён в `PjM15_PubApp/part-03/bonus-01/README.md`",
        artifact_location="PjM15_PubApp/part-03/bonus-01/README.md",
        p2p_criteria=[
            "В документе есть минимум 3 аргумента по ограничениям.",
            "Указан заказчик и ожидаемый результат.",
            "Файл размещён по указанному пути.",
        ],
        covered_outcomes=["Аргументировать проектное решение"],
        theory_support=["Критерии выбора решения"],
    )

    result = PracticeChecks(language="ru", check_task_count=False).check([task])

    assert not any(issue.criterion_id == "2.5.1" for issue in result.all_issues)
    assert not result.hard_issues
