// Generator form state helpers.
// This module owns DOM <-> generation seed mapping; main.js owns orchestration only.

        function callGeneratorFormHook(name) {
            const fn = window[name];
            if (typeof fn === 'function') {
                fn();
            }
        }

        function getInputValue(id, fallback = '') {
            const element = document.getElementById(id);
            return element ? element.value : fallback;
        }

        function setInputValue(id, value) {
            if (typeof setValue === 'function') {
                setValue(id, value);
                return;
            }
            const element = document.getElementById(id);
            if (element) {
                element.value = value ?? '';
            }
        }

        function getTrimmedValue(id) {
            return getInputValue(id, '').trim();
        }

        function listFieldValue(value, separator) {
            if (Array.isArray(value)) {
                return value.join(separator);
            }
            return value ?? '';
        }

        function splitLines(value) {
            return String(value || '')
                .split('\n')
                .map((item) => item.trim())
                .filter(Boolean);
        }

        const STORYTELLING_TYPE_HELP = {
            sjm: 'По умолчанию. SJM применяется прежде всего к практической части: ситуациям, задачам, артефактам и критериям. Теория только поддерживает этот контекст.',
            case: 'SJM воспринимается как рабочий кейс: проблема, вводные, ограничения и проверяемый результат для практических заданий.',
            role_play: 'Акцент на роли студента. Задания формулируются так, будто студент действует из заданной профессиональной роли.',
            project_scenario: 'SJM задаёт ход проекта: от вводных к действиям, артефактам и финальному результату.',
            story_arc: 'SJM связывает README в единую линию повествования. Используй, если нужна цельная история через теорию и практику.',
            none: 'Сторителлинг отключён. Генератор опирается только на описание проекта, образовательные результаты, навыки и инструменты.',
        };

        const FIELD_HELP_TEXT = {
            direction: 'Направление определяет ветку учебного плана и влияет на словарь, контекст соседних проектов и ожидаемые артефакты.',
            curriculumBlock: 'Тематический блок задаёт место проекта в программе. По нему подтягиваются проекты из загруженного CSV.',
            curriculumProject: 'Выберите проект из учебного плана, чтобы автоматически заполнить результаты обучения, навыки, инструменты и описание.',
            projectType: 'Тип проекта влияет на формулировки задач: индивидуальная работа или распределение ролей в группе.',
            groupSize: 'Количество участников используется только для групповых проектов и помогает подобрать командные артефакты.',
            audienceLevel: 'Уровень аудитории задаёт сложность объяснений, глубину теории и ожидаемую самостоятельность студента.',
            titleSeed: 'Рабочее название проекта. Если оставить пустым, генератор возьмёт название из учебного плана или соберёт его из контекста.',
            projectDescription: 'Кратко опишите суть проекта, рабочий кейс и основной результат. Это главный смысловой вход для генератора.',
            requiredTools: 'Инструменты, которые студент должен использовать или упомянуть в проекте: сервисы, методики, библиотеки, форматы.',
            requiredSoftware: 'ПО и среда, которые нужны для выполнения проекта: редакторы, базы данных, браузеры, контейнеры и другие приложения.',
            storytelling: 'Опишите рабочую ситуацию, роль студента, ограничения и конфликт. Этот текст связывает теорию и практику.',
            learningOutcomes: 'Список образовательных результатов. По ним генератор проверяет, что теория и практика закрывают цели обучения.',
            skills: 'Навыки, которые студент должен получить. Они помогают настроить акценты задач и критериев проверки.',
            bonusWish: 'Дополнительный запрос к бонусному заданию: тема, формат или ограничение, которое нужно учесть.',
            platformName: 'Название проекта на платформе. Используется как метаданные и может отличаться от публичного заголовка README.',
            workloadHours: 'Оценка трудоёмкости помогает подобрать объём теории, количество задач и реалистичность практики.',
            additionalMaterials: 'Опорные материалы, выдержки и ссылки, которые можно использовать как контекст, но не как обязательный итоговый артефакт.',
            projectContentType: 'Классификация помогает выбрать стиль практики: код, данные, QA/DevOps или no-code рабочий кейс.',
            repoBaseUrl: 'Базовая ссылка на репозиторий нужна для корректных путей и ссылок, если проект должен ссылаться на внешний репозиторий.',
            repoPathTemplate: 'Шаблон пути задаёт, где студент должен размещать результаты задач. {num:02d} заменяется номером задания.',
            newBlockName: 'Название нового направления появится в списке направлений и поможет сгруппировать проекты учебного плана.',
            newBlockCode: 'Короткий код направления используется в технических идентификаторах и фильтрах.',
        };

        function getFieldHelpText(fieldId) {
            if (fieldId === 'storytellingType') {
                const select = document.getElementById('storytellingType');
                const selected = select ? select.value : 'sjm';
                return STORYTELLING_TYPE_HELP[selected] || STORYTELLING_TYPE_HELP.sjm;
            }
            return FIELD_HELP_TEXT[fieldId] || '';
        }

        function setHelpTriggerText(trigger, fieldId) {
            const text = getFieldHelpText(fieldId);
            const popover = document.getElementById(`${fieldId}Help`);
            if (!text || !trigger) return;

            trigger.setAttribute('title', text);
            trigger.setAttribute('aria-label', `Пояснение поля: ${text}`);
            if (popover) {
                popover.textContent = text;
            }
        }

        function ensureFieldHelpTrigger(fieldId) {
            const field = document.getElementById(fieldId);
            const text = getFieldHelpText(fieldId);
            if (!field || !text) return;

            const group = field.closest('.form-group');
            const label = document.querySelector(`label[for="${fieldId}"]`) || group?.querySelector(':scope > label');
            if (!label) return;

            label.classList.add('field-help-label');
            let trigger = document.getElementById(`${fieldId}HelpTrigger`);
            if (fieldId === 'storytellingType') {
                trigger = document.getElementById('storytellingTypeHelpTrigger') || trigger;
            }
            if (!trigger) {
                trigger = document.createElement('button');
                trigger.className = 'field-help-trigger';
                trigger.id = `${fieldId}HelpTrigger`;
                trigger.type = 'button';
                trigger.textContent = '?';
                label.appendChild(trigger);
            }
            trigger.classList.add('field-help-trigger');
            trigger.dataset.helpField = fieldId;

            let popover = document.getElementById(`${fieldId}Help`);
            if (!popover) {
                popover = document.createElement('span');
                popover.className = 'field-help-popover';
                popover.id = `${fieldId}Help`;
                popover.setAttribute('role', 'tooltip');
                popover.dataset.fieldHelpPopover = fieldId;
                label.appendChild(popover);
            }
            trigger.setAttribute('aria-describedby', popover.id);
            setHelpTriggerText(trigger, fieldId);

            if (trigger.dataset.fieldHelpReady === 'true') return;
            trigger.dataset.fieldHelpReady = 'true';
            trigger.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                document.querySelectorAll('.field-help-popover.is-visible').forEach((item) => {
                    if (item !== popover) item.classList.remove('is-visible');
                });
                popover.classList.toggle('is-visible');
            });
            trigger.addEventListener('blur', () => {
                window.setTimeout(() => popover.classList.remove('is-visible'), 120);
            });
        }

        function updateStorytellingTypeHelp() {
            const trigger = document.getElementById('storytellingTypeHelpTrigger');
            setHelpTriggerText(trigger, 'storytellingType');
        }

        function initializeGeneratorFieldHelp() {
            Object.keys(FIELD_HELP_TEXT).forEach(ensureFieldHelpTrigger);
            ensureFieldHelpTrigger('storytellingType');
        }

        function initializeStorytellingTypeHelp() {
            const select = document.getElementById('storytellingType');
            initializeGeneratorFieldHelp();
            if (!select || select.dataset.storytellingHelpReady === 'true') {
                updateStorytellingTypeHelp();
                return;
            }

            select.dataset.storytellingHelpReady = 'true';
            select.addEventListener('change', updateStorytellingTypeHelp);
            updateStorytellingTypeHelp();
        }

function restoreFormData(seed) {
            // Восстанавливаем значения полей формы
            if (seed.project_type) setInputValue('projectType', seed.project_type);
            if (seed.direction) setInputValue('direction', seed.direction);
            if (seed.thematic_block) setInputValue('thematicBlock', seed.thematic_block);
            if (seed.audience_level) setAudienceLevel(seed.audience_level);
            if (seed.required_tools) setInputValue('requiredTools', listFieldValue(seed.required_tools, ', '));
            if (seed.required_software) setInputValue(
                'requiredSoftware',
                listFieldValue(seed.required_software, ', ')
            );
            if (seed.title_seed) setInputValue('titleSeed', seed.title_seed);
            if (seed.project_description) setInputValue('projectDescription', seed.project_description);
            if (seed.storytelling_type) setInputValue('storytellingType', seed.storytelling_type);
            if (seed.sjm) setInputValue('storytelling', seed.sjm);
            if (seed.learning_outcomes) setInputValue('learningOutcomes', listFieldValue(seed.learning_outcomes, '\n'));
            if (seed.skills) setInputValue('skills', listFieldValue(seed.skills, '\n'));
            if (seed.group_size) setInputValue('groupSize', seed.group_size);
            if (seed.repo_path_template) setInputValue('repoPathTemplate', seed.repo_path_template);
            if (seed.repo_base_url) setInputValue('repoBaseUrl', seed.repo_base_url);
            if (seed.platform_name) setInputValue('platformName', seed.platform_name);
            if (seed.workload_hours !== undefined && seed.workload_hours !== null) setInputValue('workloadHours', seed.workload_hours);
            if (seed.additional_materials) setInputValue('additionalMaterials', seed.additional_materials);
            if (seed.project_content_type) {
                setInputValue('projectContentType', seed.project_content_type === 'auto' ? '' : seed.project_content_type);
            } else if (seed.is_programming_project !== undefined && seed.is_programming_project !== null) {
                setInputValue('projectContentType', seed.is_programming_project ? 'hard_code' : 'no_code');
            }
            
            // Восстанавливаем чекбоксы
            setChecked('methodologyHumanReview', !!seed.methodology_human_review);
            setChecked('includeFormulas', !!seed.include_formulas);
            setChecked('includeTables', !!seed.include_tables);
            setChecked('includeDiagrams', !!seed.include_diagrams);
            if (seed.bonus_wish !== null && seed.bonus_wish !== undefined) {
                setChecked('generateBonus', true);
                setInputValue('bonusWish', seed.bonus_wish || '');
                callGeneratorFormHook('toggleBonusWish');
            }
            callGeneratorFormHook('toggleGroupSize');
            updateStorytellingTypeHelp();
            
        }
        // Направления (ранее thematicBlocks)

function fillFormFromData(data) {
            if (data.project_type) {
                const projectType = data.project_type === 'индивидуальный' ? 'individual' : 
                                  data.project_type === 'групповой' ? 'group' : data.project_type;
                setInputValue('projectType', projectType);
                callGeneratorFormHook('toggleGroupSize');
            }
            if (data.thematic_block || data.track) {
                setInputValue('thematicBlock', data.thematic_block || data.track);
            }
            if (data.direction) setInputValue('direction', data.direction);
            if (data.audience_level) setAudienceLevel(data.audience_level);
            // Маппинг: project_title -> title_seed для обратной совместимости
            if (data.title_seed || data.project_title) {
                setInputValue('titleSeed', data.title_seed || data.project_title);
            }
            if (data.required_tools) {
                setInputValue('requiredTools', listFieldValue(data.required_tools, ', '));
            }
            if (data.required_software) {
                setInputValue(
                    'requiredSoftware',
                    listFieldValue(data.required_software, ', ')
                );
            }
            if (data.storytelling_type) setInputValue('storytellingType', data.storytelling_type);
            if (data.sjm) setInputValue('storytelling', data.sjm);
            if (data.methodology_human_review !== undefined) {
                setChecked('methodologyHumanReview', !!data.methodology_human_review);
            }
            if (data.project_description) setInputValue('projectDescription', data.project_description);
            if (data.learning_outcomes) {
                setInputValue('learningOutcomes', listFieldValue(data.learning_outcomes, '\n'));
            }
            if (data.skills) {
                setInputValue('skills', listFieldValue(data.skills, '\n'));
            }
            if (data.group_size) {
                setInputValue('groupSize', data.group_size);
                callGeneratorFormHook('toggleGroupSize');
            }
            if (data.repo_base_url) setInputValue('repoBaseUrl', data.repo_base_url);
            if (data.repo_path_template) setInputValue('repoPathTemplate', data.repo_path_template);
            if (data.platform_name) setInputValue('platformName', data.platform_name);
            if (data.workload_hours !== undefined && data.workload_hours !== null) setInputValue('workloadHours', data.workload_hours);
            if (data.additional_materials) setInputValue('additionalMaterials', data.additional_materials);
            if (data.project_content_type) {
                setInputValue('projectContentType', data.project_content_type === 'auto' ? '' : data.project_content_type);
            } else if (data.is_programming_project !== undefined && data.is_programming_project !== null) {
                setInputValue('projectContentType', data.is_programming_project ? 'hard_code' : 'no_code');
            }
            setChecked('includeFormulas', !!data.include_formulas);
            setChecked('includeTables', !!data.include_tables);
            setChecked('includeDiagrams', !!data.include_diagrams);
            if (data.bonus_wish) {
                setChecked('generateBonus', true);
                setInputValue('bonusWish', data.bonus_wish);
                callGeneratorFormHook('toggleBonusWish');
            }
            updateStorytellingTypeHelp();
        }

        function applyOptionalTextSeedField(seed, fieldId, seedKey) {
            const value = getTrimmedValue(fieldId);
            if (value) {
                seed[seedKey] = value;
            } else {
                delete seed[seedKey];
            }
        }

        function applyOptionalNumberSeedField(seed, fieldId, seedKey, parser) {
            const rawValue = getTrimmedValue(fieldId);
            if (!rawValue) {
                delete seed[seedKey];
                return;
            }
            const value = parser(rawValue);
            if (!Number.isNaN(value)) {
                seed[seedKey] = value;
            }
        }

        function readCurriculumContext(explicitContext = null) {
            if (explicitContext) return explicitContext;
            try {
                const savedContext = sessionStorage.getItem('curriculum_context');
                return savedContext ? JSON.parse(savedContext) : null;
            } catch (error) {
                console.warn('Failed to restore curriculum_context');
                return null;
            }
        }

        function getSelectedGenerationProvider() {
            return typeof window.getSelectedLlmProvider === 'function'
                ? window.getSelectedLlmProvider()
                : 'polza';
        }

        function applySelectedCurriculumProject(seed) {
            const curriculumProjectSelect = document.getElementById('curriculumProject');
            if (!curriculumProjectSelect || !curriculumProjectSelect.value) return;
            const selectedOption = curriculumProjectSelect.selectedOptions[0];
            if (!selectedOption || !selectedOption.dataset.project) return;
            try {
                const projectData = JSON.parse(selectedOption.dataset.project);
                if (projectData.platform_name) seed.platform_name = projectData.platform_name;
                if (projectData.workload_hours !== undefined && projectData.workload_hours !== null) {
                    seed.workload_hours = projectData.workload_hours;
                }
                if (projectData.additional_materials) seed.additional_materials = projectData.additional_materials;
                if (projectData.storytelling_type) seed.storytelling_type = projectData.storytelling_type;
                if (!seed.sjm && projectData.sjm) seed.sjm = projectData.sjm;
            } catch (error) {
                console.warn('Failed to parse selected curriculum project:', error);
            }
        }

        function buildGenerationSeed({ curriculumContext = null } = {}) {
            const directionValue = getInputValue('direction');
            const thematicBlockValue = getInputValue('thematicBlock') || getInputValue('curriculumBlock');
            const seed = {
                language: 'ru',
                llm_provider: getSelectedGenerationProvider(),
                project_type: getInputValue('projectType'),
                direction: directionValue !== 'ADD' ? directionValue : '',
                thematic_block: thematicBlockValue || directionValue,
                audience_level: normalizeAudienceLevel(getInputValue('audienceLevel')),
                required_tools: splitCommaList(getInputValue('requiredTools')),
                required_software: splitCommaList(getInputValue('requiredSoftware')),
                title_seed: getInputValue('titleSeed'),
                project_description: getInputValue('projectDescription'),
                learning_outcomes: splitLines(getInputValue('learningOutcomes')),
                skills: splitLines(getInputValue('skills')),
                storytelling_type: getInputValue('storytellingType') || 'sjm',
                sjm: getTrimmedValue('storytelling') || null,
                methodology_human_review: getChecked('methodologyHumanReview'),
                include_formulas: getChecked('includeFormulas'),
                include_tables: getChecked('includeTables'),
                include_diagrams: getChecked('includeDiagrams'),
            };

            const projectContentType = getInputValue('projectContentType');
            if (projectContentType) {
                seed.project_content_type = projectContentType;
                seed.is_programming_project = projectContentType === 'hard_code'
                    ? true
                    : projectContentType === 'no_code'
                        ? false
                        : null;
            }

            const resolvedCurriculumContext = readCurriculumContext(curriculumContext);
            if (resolvedCurriculumContext) seed.curriculum_context = resolvedCurriculumContext;
            applySelectedCurriculumProject(seed);

            applyOptionalTextSeedField(seed, 'platformName', 'platform_name');
            applyOptionalTextSeedField(seed, 'additionalMaterials', 'additional_materials');
            applyOptionalNumberSeedField(seed, 'workloadHours', 'workload_hours', parseFloat);

            if (seed.project_type === 'group') {
                seed.group_size = parseInt(getInputValue('groupSize'), 10);
            }
            applyOptionalTextSeedField(seed, 'repoBaseUrl', 'repo_base_url');
            applyOptionalTextSeedField(seed, 'repoPathTemplate', 'repo_path_template');

            seed.bonus_wish = getChecked('generateBonus') ? (getTrimmedValue('bonusWish') || '') : null;
            return seed;
        }


        if (typeof window !== 'undefined') {
            Object.assign(window, {
                restoreFormData,
                fillFormFromData,
                buildGenerationSeed,
                initializeGeneratorFieldHelp,
                initializeStorytellingTypeHelp,
                updateStorytellingTypeHelp,
            });
        }


