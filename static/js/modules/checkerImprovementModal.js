// README improvement modal: extraction, editable seed form and form helpers.

let improvementThematicBlocks = {
    "Бизнес аналитика": "BSA",
    "Кибербезопасность": "Cb",
    "DevOps": "DO",
    "Проектный менеджмент": "PjM",
    "Тестирование и обеспечение качества": "QA",
    "Машинное обучение": "DS"
};

function checkerImprovementApiUrl() {
    return window.API_URL || (window.API_BASE ? `${window.API_BASE}/api/v1` : '/api/v1');
}

function setValue(id, value) {
    const element = document.getElementById(id);
    if (element) element.value = value || '';
}

function setChecked(id, value) {
    const element = document.getElementById(id);
    if (element) element.checked = !!value;
}

async function startImprovement() {
    const originalReadme = window.originalReadmeForImprovement;
    if (!originalReadme) {
        alert('Ошибка: исходный README не найден. Пожалуйста, проверьте README сначала.');
        return;
    }

    const modal = document.getElementById('improvementModal');
    const loading = document.getElementById('improvementLoading');
    const editor = document.getElementById('improvementEditor');

    if (modal) modal.style.display = 'block';
    if (loading) loading.style.display = 'block';
    if (editor) editor.style.display = 'none';

    try {
        await extractDataForImprovement(originalReadme);
    } catch (error) {
        console.error('Ошибка при извлечении данных:', error);
        alert(`Ошибка при извлечении данных: ${error.message}`);
        if (loading) loading.style.display = 'none';
    }
}

async function extractDataForImprovement(readmeText) {
    const loading = document.getElementById('improvementLoading');
    const editor = document.getElementById('improvementEditor');
    const authHeaders = window.getAuthHeadersForImprovement?.() || {};

    if (!authHeaders['Authorization']) {
        alert('Ошибка: требуется авторизация. Пожалуйста, войдите в систему.');
        return;
    }

    const body = { readme_text: readmeText };
    const curriculumPayload = window.getCheckerCurriculumPayload?.();
    if (curriculumPayload) {
        body.curriculum_project = curriculumPayload.curriculum_project;
        body.curriculum_context = curriculumPayload.curriculum_context;
    }

    try {
        const response = await fetch(`${checkerImprovementApiUrl()}/readme/improve/extract`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...authHeaders
            },
            body: JSON.stringify(body)
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Ошибка извлечения данных' }));
            throw new Error(error.detail || `Ошибка ${response.status}`);
        }

        const data = await response.json();
        window.setCheckerImprovementRequestId?.(data.request_id);
        showImprovementEditor(data);
    } catch (error) {
        console.error('Ошибка извлечения данных:', error);
        if (loading) loading.style.display = 'none';
        if (editor) editor.style.display = 'block';

        clearImprovementForm();
        showImprovementWarning(
            '<div class="error-msg">' +
            '<strong>Ошибка извлечения данных:</strong><br>' +
            (error.message || 'Ошибка извлечения данных') + '<br><br>' +
            '<small>Поля формы оставлены пустыми. Пожалуйста, заполните их вручную.</small>' +
            '</div>'
        );
    }
}

async function loadImprovementThematicBlocks() {
    try {
        const authHeaders = window.getAuthHeadersForImprovement?.() || {};
        const response = await fetch(`${checkerImprovementApiUrl()}/thematic-blocks`, {
            headers: authHeaders
        });
        if (response.ok) {
            const blocks = await response.json();
            if (blocks && typeof blocks === 'object') {
                improvementThematicBlocks = blocks;
                updateImproveThematicBlockSelect();
            }
        }
    } catch (error) {
        console.log('Используем тематические блоки по умолчанию для улучшения');
    }
}

function updateImproveThematicBlockSelect() {
    const select = document.getElementById('improveThematicBlock');
    if (!select) return;

    const currentValue = select.value;
    select.innerHTML = '<option value="">Выберите тематический блок</option>';

    for (const [name, code] of Object.entries(improvementThematicBlocks)) {
        const option = document.createElement('option');
        option.value = code;
        option.textContent = name;
        select.appendChild(option);
    }

    const addOption = document.createElement('option');
    addOption.value = 'ADD';
    addOption.textContent = 'Добавить новый';
    select.appendChild(addOption);

    if (currentValue && currentValue !== 'ADD') {
        select.value = currentValue;
    }

    select.onchange = function() {
        const expander = document.getElementById('improveAddBlockExpander');
        if (expander) {
            expander.style.display = this.value === 'ADD' ? 'block' : 'none';
        }
    };
}

async function addImprovementThematicBlock() {
    const name = document.getElementById('improveNewBlockName')?.value.trim() || '';
    const code = document.getElementById('improveNewBlockCode')?.value.trim() || '';

    if (!name) {
        alert('Введите название тематического блока');
        return;
    }
    if (!code) {
        alert('Введите кодовое обозначение');
        return;
    }
    if (Object.values(improvementThematicBlocks).includes(code)) {
        alert(`Кодовое обозначение '${code}' уже используется!`);
        return;
    }

    improvementThematicBlocks[name] = code;
    updateImproveThematicBlockSelect();
    setValue('improveThematicBlock', code);
    const expander = document.getElementById('improveAddBlockExpander');
    if (expander) expander.style.display = 'none';
    setValue('improveNewBlockName', '');
    setValue('improveNewBlockCode', '');

    try {
        const authHeaders = window.getAuthHeadersForImprovement?.() || {};
        await fetch(`${checkerImprovementApiUrl()}/thematic-blocks`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...authHeaders
            },
            body: JSON.stringify(improvementThematicBlocks)
        });
    } catch (error) {
        console.log('Не удалось сохранить тематические блоки на сервере');
    }
}

function toggleImproveBonusWish() {
    const generateBonus = document.getElementById('improveGenerateBonus')?.checked;
    const group = document.getElementById('improveBonusWishGroup');
    if (group) {
        group.style.display = generateBonus ? 'block' : 'none';
    }
}

function ensureToggleExpanderFallback() {
    if (typeof window.toggleExpander === 'function') return;

    window.toggleExpander = function(id) {
        const contentElement = document.getElementById(id);
        if (!contentElement) {
            console.error(`Элемент с ID "${id}" не найден`);
            return;
        }

        let expander = contentElement.closest('.expander') || document.getElementById(id);
        if (!expander) {
            console.error(`Expander с ID "${id}" не найден`);
            return;
        }

        const header = expander.querySelector('.expander-header');
        const content = expander.querySelector('.expander-content');
        if (!content) {
            console.error(`Expander content не найден для "${id}"`);
            return;
        }

        const isVisible = content.style.display !== 'none';
        content.style.display = isVisible ? 'none' : 'block';

        const arrow = header?.querySelector('span:last-child');
        if (arrow) {
            arrow.textContent = isVisible ? '▼' : '▲';
        }
    };
}

function showImprovementEditor(data) {
    const loading = document.getElementById('improvementLoading');
    const editor = document.getElementById('improvementEditor');
    if (loading) loading.style.display = 'none';
    if (editor) editor.style.display = 'block';

    const partialSeed = data.partial_seed || {};
    const classification = data.classification || {};

    loadImprovementThematicBlocks().then(() => {
        setValue('improveTitleSeed', partialSeed.title_seed);
        setValue('improveDescription', partialSeed.project_description);
        setValue('improveLanguage', classification.language || 'ru');
        setImproveThematicBlock(classification);
        setValue('improveAudienceLevel', classification.audience_level || 'base');

        const projectType = classification.project_type || 'individual';
        setValue('improveProjectType', projectType);
        setValue('improveLearningOutcomes', (partialSeed.learning_outcomes || []).join('\n'));
        setValue('improveSkills', (partialSeed.skills || []).join(', '));
        setValue('improveRequiredTools', (partialSeed.required_tools || []).join(', '));
        setValue('improveTasksCount', partialSeed.tasks_count || '');

        if (projectType === 'group' && partialSeed.group_size) {
            setValue('improveGroupSize', partialSeed.group_size);
        }

        toggleImproveGroupSize();
        setValue('improveZUN', '');
        setChecked('improveGenerateBonus', false);
        setValue('improveBonusWish', '');
        setValue('improveRepoBaseUrl', '');
        setValue('improveRepoPathTemplate', 'repo/part-03/task-{num:02d}/README.md');
        toggleImproveBonusWish();
    });

    const hint = document.getElementById('thematicBlockHint');
    if (hint && classification.thematic_block_suggested) {
        hint.textContent = `Предложен новый блок: ${classification.thematic_block_name || classification.thematic_block_suggested}`;
    }

    if (data.metadata?.warnings?.length > 0) {
        showImprovementWarning(
            '<div class="warnings">' +
            '<strong>Предупреждения:</strong><ul>' +
            data.metadata.warnings.map(warning => `<li>${warning}</li>`).join('') +
            '</ul></div>'
        );
    }
}

function setImproveThematicBlock(classification) {
    const thematicBlockCode = classification.thematic_block_suggested || classification.thematic_block || '';
    const select = document.getElementById('improveThematicBlock');
    if (!select) return;

    if (thematicBlockCode && Object.values(improvementThematicBlocks).includes(thematicBlockCode)) {
        select.value = thematicBlockCode;
        return;
    }

    select.value = 'ADD';
    if (thematicBlockCode) {
        const expander = document.getElementById('improveAddBlockExpander');
        if (expander) expander.style.display = 'block';

        if (classification.thematic_block_suggested && classification.thematic_block_name) {
            setValue('improveNewBlockCode', classification.thematic_block_suggested);
            setValue('improveNewBlockName', classification.thematic_block_name);
        } else {
            setValue('improveNewBlockCode', thematicBlockCode);
        }
    }
}

function showImprovementWarning(html) {
    const warningsDiv = document.getElementById('improvementWarnings');
    if (warningsDiv) {
        warningsDiv.innerHTML = html;
    }
}

function closeImprovementModal() {
    const modal = document.getElementById('improvementModal');
    if (modal) modal.style.display = 'none';
}

function initializeTabs() {
    document.querySelectorAll('.tab-content').forEach(content => {
        content.style.display = content.classList.contains('active') ? 'block' : 'none';
    });
}

function toggleImproveGroupSize() {
    const projectType = document.getElementById('improveProjectType');
    const groupSizeGroup = document.getElementById('improveGroupSizeGroup');
    const groupSizeInput = document.getElementById('improveGroupSize');

    if (!projectType || !groupSizeGroup || !groupSizeInput) return;
    if (projectType.value === 'group') {
        groupSizeGroup.style.display = 'block';
        groupSizeInput.required = true;
    } else {
        groupSizeGroup.style.display = 'none';
        groupSizeInput.required = false;
        groupSizeInput.value = '';
    }
}

function clearImprovementForm() {
    const form = document.getElementById('improvementForm');
    if (form) form.reset();

    setValue('improveZUN', '');
    setChecked('improveGenerateBonus', false);
    setValue('improveBonusWish', '');
    setValue('improveRepoBaseUrl', '');
    setValue('improveRepoPathTemplate', 'repo/part-03/task-{num:02d}/README.md');
    setValue('improveGroupSize', '2');
    toggleImproveBonusWish();
    toggleImproveGroupSize();
    showImprovementWarning('');
}

function bindImprovementModalEvents() {
    initializeTabs();

    const form = document.getElementById('improvementForm');
    if (form) {
        form.addEventListener('submit', async function(event) {
            event.preventDefault();
            await window.generateImprovedReadme?.();
        });
    }

    const modal = document.getElementById('improvementModal');
    if (modal) {
        modal.addEventListener('click', function(event) {
            if (event.target === modal) {
                closeImprovementModal();
            }
        });
    }
}

ensureToggleExpanderFallback();
document.addEventListener('DOMContentLoaded', bindImprovementModalEvents);

window.startImprovement = startImprovement;
window.extractDataForImprovement = extractDataForImprovement;
window.addImprovementThematicBlock = addImprovementThematicBlock;
window.toggleImproveBonusWish = toggleImproveBonusWish;
window.loadImprovementThematicBlocks = loadImprovementThematicBlocks;
window.updateImproveThematicBlockSelect = updateImproveThematicBlockSelect;
window.closeImprovementModal = closeImprovementModal;
window.initializeTabs = initializeTabs;
window.toggleImproveGroupSize = toggleImproveGroupSize;
window.clearImprovementForm = clearImprovementForm;
