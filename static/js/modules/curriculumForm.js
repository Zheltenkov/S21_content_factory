// Curriculum CSV and project selector controller.
// Owns curriculum session state and exposes a small global contract for main.js.

let curriculumDirections = {
    "Бизнес аналитика": "BSA",
    "Кибербезопасность": "Cb",
    "DevOps": "DO",
    "Проектный менеджмент": "PjM",
    "Тестирование и обеспечение качества": "QA",
    "Машинное обучение": "DS"
};

let currentCurriculum = null;
let currentCurriculumContext = null;
let persistedCurriculumPlans = [];

function getCurriculumApiUrl() {
    const runtime = window.ContentGenGenerationRuntime || {};
    if (typeof runtime.getApiUrl === 'function') return runtime.getApiUrl();
    return window.ContentGenApiUrl || window.API_URL || `${window.location.origin}/api/v1`;
}

function getCurriculumAuthHeader() {
    const token = localStorage.getItem('auth_token');
    return token ? { Authorization: `Bearer ${token}` } : {};
}

function getCurrentCurriculumContext() {
    if (currentCurriculumContext) return currentCurriculumContext;
    try {
        const savedContext = sessionStorage.getItem('curriculum_context');
        currentCurriculumContext = savedContext ? JSON.parse(savedContext) : null;
        return currentCurriculumContext;
    } catch (error) {
        console.warn('Failed to restore curriculum_context');
        return null;
    }
}

function setPersistedPlanStatus(message, isError = false) {
    const statusEl = document.getElementById('persistedCurriculumPlanStatus');
    if (!statusEl) return;
    statusEl.textContent = message || '';
    statusEl.style.color = isError ? '#c9342b' : '';
}

function syncDirectionFromCurriculum(curriculum) {
    const directionCode = curriculum?.direction_code;
    const directionSelect = document.getElementById('direction');
    if (!directionSelect || !directionCode || directionCode === 'UNK') return;

    const hasOption = Array.from(directionSelect.options).some(opt => opt.value === directionCode);
    if (hasOption) directionSelect.value = directionCode;
}

function applyCurriculumData(curriculum, sourceLabel, options = {}) {
    if (!curriculum || !Array.isArray(curriculum.blocks)) {
        throw new Error('УП сохранен без блоков и проектов');
    }

    currentCurriculum = curriculum;
    if (options.persist !== false) {
        sessionStorage.setItem('curriculum_data', JSON.stringify(currentCurriculum));
    }
    if (options.clearContext !== false) {
        currentCurriculumContext = null;
        sessionStorage.removeItem('curriculum_context');
    }

    syncDirectionFromCurriculum(currentCurriculum);
    populateCurriculumBlocks();

    const blockGroup = document.getElementById('curriculumBlockGroup');
    if (blockGroup) blockGroup.style.display = 'block';

    const fileNameEl = document.getElementById('curriculumFileName');
    if (fileNameEl) {
        fileNameEl.textContent = `${sourceLabel} (${currentCurriculum.blocks.length} блоков)`;
    }
}

async function handleCurriculumUpload(event) {
    const fileInput = event && event.target ? event.target : null;
    const file = fileInput && fileInput.files ? fileInput.files[0] : null;
    if (!file) return;

    const fileNameEl = document.getElementById('curriculumFileName');
    if (fileNameEl) fileNameEl.textContent = 'Загрузка...';

    const formData = new FormData();
    formData.append('file', file);

    try {
        const response = await fetch(`${getCurriculumApiUrl()}/curriculum/upload`, {
            method: 'POST',
            headers: getCurriculumAuthHeader(),
            body: formData
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Ошибка загрузки' }));
            throw new Error(error.detail || `Ошибка ${response.status}`);
        }

        const curriculum = await response.json();
        applyCurriculumData(curriculum, file.name);

        window.toast?.success(`УП загружен: ${currentCurriculum.direction} (${currentCurriculum.blocks.length} блоков)`);
        console.log('✅ УП загружен:', currentCurriculum);
    } catch (error) {
        if (fileNameEl) fileNameEl.textContent = `Ошибка: ${error.message}`;
        console.error('❌ Ошибка загрузки УП:', error);
        window.toast?.error(`Ошибка загрузки УП: ${error.message}`);
    }
}

function populateCurriculumBlocks() {
    const select = document.getElementById('curriculumBlock');
    if (!select || !currentCurriculum) return;

    select.innerHTML = '<option value="">-- Выберите блок --</option>';
    for (const block of currentCurriculum.blocks) {
        const option = document.createElement('option');
        option.value = block.name;
        option.textContent = block.name;
        option.dataset.goals = JSON.stringify(block.goals || []);
        select.appendChild(option);
    }
}

function onCurriculumBlockChange() {
    const blockSelect = document.getElementById('curriculumBlock');
    const projectGroup = document.getElementById('curriculumProjectGroup');
    const projectSelect = document.getElementById('curriculumProject');
    const blockName = blockSelect?.value || '';

    const thematicBlock = document.getElementById('thematicBlock');
    if (thematicBlock) thematicBlock.value = blockName;

    if (!blockName || !currentCurriculum || !projectGroup || !projectSelect) {
        if (projectGroup) projectGroup.style.display = 'none';
        return;
    }

    const block = currentCurriculum.blocks.find(b => b.name === blockName);
    if (!block) return;

    projectSelect.innerHTML = '<option value="">-- Выберите проект --</option>';
    for (const project of block.projects) {
        const option = document.createElement('option');
        option.value = project.order;
        option.textContent = `${project.order}. ${project.title}`;
        option.dataset.project = JSON.stringify({
            ...project,
            source_plan_id: currentCurriculum.source_plan_id || null,
            plan_version: currentCurriculum.plan_version || null,
            plan_hash: currentCurriculum.plan_hash || null
        });
        projectSelect.appendChild(option);
    }

    projectGroup.style.display = 'block';
}

async function onCurriculumProjectChange() {
    const projectSelect = document.getElementById('curriculumProject');
    const selectedOption = projectSelect?.selectedOptions?.[0];
    if (!selectedOption || !selectedOption.dataset.project || !currentCurriculum) return;

    const project = JSON.parse(selectedOption.dataset.project);
    const blockName = document.getElementById('curriculumBlock')?.value || '';
    const block = currentCurriculum.blocks.find(b => b.name === blockName);
    if (!block) return;

    setCurriculumFieldValue('titleSeed', project.title || '');
    setCurriculumFieldValue('projectDescription', project.description || '');

    if (project.learning_outcomes && project.learning_outcomes.length > 0) {
        setCurriculumFieldValue('learningOutcomes', project.learning_outcomes.join('\n'));
    }

    setCurriculumFieldValue(
        'skills',
        project.skills && project.skills.length > 0 ? project.skills.join('\n') : ''
    );

    const audienceLevelEl = document.getElementById('audienceLevel');
    if (audienceLevelEl) {
        audienceLevelEl.value = window.normalizeAudienceLevel
            ? window.normalizeAudienceLevel(project.audience_level)
            : (project.audience_level || 'beginner_plus');
    }

    setCurriculumFieldValue(
        'requiredTools',
        project.required_tools && project.required_tools.length > 0 ? project.required_tools.join(', ') : ''
    );
    setCurriculumFieldValue('requiredSoftware', project.required_software || '');
    setCurriculumFieldValue('storytellingType', project.storytelling_type || 'sjm');
    window.updateStorytellingTypeHelp?.();
    setCurriculumFieldValue('storytelling', project.sjm || '');

    if (project.format) {
        setCurriculumFieldValue('projectType', project.format);
        window.toggleGroupSize?.();
    }
    if (project.group_size) {
        setCurriculumFieldValue('groupSize', project.group_size);
    }

    setCurriculumFieldValue('platformName', project.platform_name || project.title || '');
    setCurriculumFieldValue('workloadHours', project.workload_hours || '');
    setCurriculumFieldValue('additionalMaterials', project.additional_materials || '');

    if (block.code && block.code !== 'UNK') {
        setCurriculumFieldValue('direction', block.code);
    }

    try {
        currentCurriculumContext = await buildCurriculumContext(block, project);
        sessionStorage.setItem('curriculum_context', JSON.stringify(currentCurriculumContext));
    } catch (error) {
        currentCurriculumContext = null;
        sessionStorage.removeItem('curriculum_context');
        console.error('❌ Контекст УП не готов:', error);
        window.toast?.error(error.message || 'УП не готов к генерации');
        return;
    }

    console.log('✅ Данные проекта загружены из УП:', project.title);
    console.log('📋 Контекст УП:', currentCurriculumContext);
    window.toast?.success(`Загружен проект: ${project.title}`);
}

function setCurriculumFieldValue(id, value) {
    const element = document.getElementById(id);
    if (element) element.value = value ?? '';
}

function buildLocalCurriculumContext(block, currentProject) {
    if (!currentCurriculum || !block || !currentProject) return null;

    const blockIndex = currentCurriculum.blocks.findIndex(b => b.name === block.name);
    const projectIndex = block.projects.findIndex(p => p.order === currentProject.order);

    const previousProjects = block.projects.slice(0, projectIndex).map(p => ({
        order: p.order,
        title: p.title,
        description: p.description,
        learning_outcomes: p.learning_outcomes || [],
        block_name: block.name
    }));

    const nextProjects = block.projects.slice(projectIndex + 1).map(p => ({
        order: p.order,
        title: p.title,
        description: p.description,
        learning_outcomes: p.learning_outcomes || [],
        block_name: block.name
    }));

    const allBlockLearningOutcomes = [];
    for (const project of block.projects) {
        if (project.learning_outcomes) {
            allBlockLearningOutcomes.push(...project.learning_outcomes);
        }
    }

    const crossBlockDepth = 2;
    let previousBlockProjects = [];
    let nextBlockProjects = [];

    if (blockIndex > 0) {
        const prevBlock = currentCurriculum.blocks[blockIndex - 1];
        previousBlockProjects = prevBlock.projects.slice(-crossBlockDepth).map(p => ({
            order: p.order,
            title: p.title,
            description: p.description,
            learning_outcomes: p.learning_outcomes || [],
            block_name: prevBlock.name
        }));
    }

    if (blockIndex < currentCurriculum.blocks.length - 1) {
        const nextBlock = currentCurriculum.blocks[blockIndex + 1];
        nextBlockProjects = nextBlock.projects.slice(0, crossBlockDepth).map(p => ({
            order: p.order,
            title: p.title,
            description: p.description,
            learning_outcomes: p.learning_outcomes || [],
            block_name: nextBlock.name
        }));
    }

    return {
        block_name: block.name,
        block_goals: block.goals || [],
        current_project_order: currentProject.order,
        current_project_description: currentProject.description || '',
        current_project_skills: currentProject.skills || [],
        current_project_audience_level: currentProject.audience_level || null,
        current_project_required_tools: currentProject.required_tools || [],
        current_project_required_software: currentProject.required_software || null,
        previous_projects: previousProjects,
        next_projects: nextProjects,
        all_block_learning_outcomes: [...new Set(allBlockLearningOutcomes)],
        previous_block_projects: previousBlockProjects,
        next_block_projects: nextBlockProjects,
        storytelling_type: currentProject.storytelling_type || 'sjm',
        sjm_context: currentProject.sjm || null,
        expert_development_notes: currentProject.expert_notes || null,
        additional_materials: currentProject.additional_materials || null
    };
}

async function buildCurriculumContext(block, currentProject) {
    if (!currentCurriculum || !block || !currentProject) return null;

    if (currentCurriculum.source_plan_id) {
        const response = await fetch(`${getCurriculumApiUrl()}/curriculum/build-context`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...getCurriculumAuthHeader()
            },
            body: JSON.stringify({
                block_name: block.name,
                project_order: currentProject.order,
                plan_id: currentCurriculum.source_plan_id,
                plan_hash: currentCurriculum.plan_hash || null,
                curriculum_data: currentCurriculum
            })
        });
        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'УП не готов к генерации' }));
            const detail = error.detail;
            const message = typeof detail === 'string'
                ? detail
                : detail?.message || 'УП не готов к генерации';
            throw new Error(message);
        }
        return response.json();
    }

    return buildLocalCurriculumContext(block, currentProject);
}

function onDirectionChange() {
    const directionSelect = document.getElementById('direction');
    const addBlockExpander = document.getElementById('addBlockExpander');
    const thematicBlock = document.getElementById('thematicBlock');
    if (!directionSelect) return;

    if (directionSelect.value === 'ADD') {
        if (addBlockExpander) addBlockExpander.style.display = 'block';
    } else {
        if (addBlockExpander) addBlockExpander.style.display = 'none';
        if (thematicBlock) thematicBlock.value = directionSelect.value;
    }
}

function restoreCurriculumFromSession() {
    try {
        const savedCurriculum = sessionStorage.getItem('curriculum_data');
        if (savedCurriculum) {
            const curriculum = JSON.parse(savedCurriculum);
            applyCurriculumData(curriculum, `${curriculum.direction} · сессия`, {
                persist: false,
                clearContext: false
            });
            console.log('📚 УП восстановлен из сессии');
        }

        currentCurriculumContext = getCurrentCurriculumContext();
        if (currentCurriculumContext) {
            console.log('📋 Контекст УП восстановлен из сессии');
        }
    } catch (error) {
        console.warn('⚠️ Не удалось восстановить УП из сессии:', error);
    }
}

async function loadThematicBlocks() {
    try {
        const response = await fetch(`${getCurriculumApiUrl()}/thematic-blocks`);
        if (response.ok) {
            curriculumDirections = await response.json();
            window.thematicBlocks = curriculumDirections;
            updateDirectionSelect();
        }
    } catch (error) {
        console.log('Используем направления по умолчанию');
    }

    restoreCurriculumFromSession();
    await loadPersistedCurriculumPlans();
}

function updateDirectionSelect() {
    const select = document.getElementById('direction');
    if (!select) return;

    const currentValue = select.value;
    select.innerHTML = '';

    for (const [name, code] of Object.entries(curriculumDirections)) {
        const option = document.createElement('option');
        option.value = code;
        option.textContent = name;
        select.appendChild(option);
    }

    const addOption = document.createElement('option');
    addOption.value = 'ADD';
    addOption.textContent = 'Добавить';
    select.appendChild(addOption);

    if (currentValue && currentValue !== 'ADD') {
        select.value = currentValue;
    }

    select.onchange = onDirectionChange;
}

function updateThematicBlockSelect() {
    updateDirectionSelect();
}

async function addThematicBlock() {
    const name = document.getElementById('newBlockName')?.value.trim() || '';
    const code = document.getElementById('newBlockCode')?.value.trim() || '';

    if (!name) {
        alert('Введите название направления');
        return;
    }
    if (!code) {
        alert('Введите кодовое обозначение');
        return;
    }
    if (Object.values(curriculumDirections).includes(code)) {
        alert(`Кодовое обозначение '${code}' уже используется!`);
        return;
    }

    curriculumDirections[name] = code;
    window.thematicBlocks = curriculumDirections;
    updateDirectionSelect();
    setCurriculumFieldValue('direction', code);
    const addBlockExpander = document.getElementById('addBlockExpander');
    if (addBlockExpander) addBlockExpander.style.display = 'none';
    setCurriculumFieldValue('newBlockName', '');
    setCurriculumFieldValue('newBlockCode', '');

    try {
        await fetch(`${getCurriculumApiUrl()}/thematic-blocks`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...getCurriculumAuthHeader()
            },
            body: JSON.stringify(curriculumDirections)
        });
    } catch (error) {
        console.log('Не удалось сохранить тематические блоки на сервере');
    }
}

async function loadPersistedCurriculumPlans() {
    const select = document.getElementById('persistedCurriculumPlan');
    if (!select) return;

    setPersistedPlanStatus('Загрузка...');
    try {
        const response = await fetch(`${getCurriculumApiUrl()}/curriculum/plans`, {
            headers: getCurriculumAuthHeader()
        });
        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Ошибка загрузки списка УП' }));
            throw new Error(error.detail || `Ошибка ${response.status}`);
        }

        const data = await response.json();
        persistedCurriculumPlans = Array.isArray(data.plans) ? data.plans : [];
        select.innerHTML = '<option value="">-- Выберите УП --</option>';

        for (const plan of persistedCurriculumPlans) {
            const option = document.createElement('option');
            option.value = plan.source_id;
            const blocks = Number(plan.blocks || 0);
            const projects = Number(plan.projects || 0);
            option.textContent = `${plan.title || `УП #${plan.source_id}`} · ${blocks} блоков / ${projects} проектов`;
            select.appendChild(option);
        }

        if (currentCurriculum?.source_plan_id) {
            select.value = String(currentCurriculum.source_plan_id);
        } else if (persistedCurriculumPlans.length === 1) {
            select.value = persistedCurriculumPlans[0].source_id;
        }

        const message = persistedCurriculumPlans.length
            ? `Доступно: ${persistedCurriculumPlans.length}`
            : 'В базе пока нет УП';
        setPersistedPlanStatus(message);
    } catch (error) {
        console.warn('Не удалось загрузить список УП из базы:', error);
        select.innerHTML = '<option value="">-- УП недоступны --</option>';
        setPersistedPlanStatus(error.message || 'Ошибка загрузки УП', true);
    }
}

async function loadPersistedCurriculumPlan(sourceId = null) {
    const select = document.getElementById('persistedCurriculumPlan');
    const selectedId = sourceId || select?.value || '';
    if (!selectedId) {
        setPersistedPlanStatus('Выберите УП', true);
        return;
    }

    setPersistedPlanStatus('Загрузка УП...');
    try {
        const response = await fetch(`${getCurriculumApiUrl()}/curriculum/plans/${encodeURIComponent(selectedId)}`, {
            headers: getCurriculumAuthHeader()
        });
        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Ошибка загрузки УП' }));
            throw new Error(error.detail || `Ошибка ${response.status}`);
        }

        const data = await response.json();
        applyCurriculumData(data.curriculum, data.plan?.title || `УП #${selectedId}`);
        setPersistedPlanStatus(`Загружен: ${data.plan?.blocks || currentCurriculum.blocks.length} блоков`);
        if (data.readiness && data.readiness.ready === false) {
            setPersistedPlanStatus('Есть блокеры перед генерацией', true);
        }
        window.toast?.success(`УП загружен из базы: ${data.plan?.title || currentCurriculum.direction}`);
        console.log('✅ УП загружен из базы:', currentCurriculum);
    } catch (error) {
        console.error('Ошибка загрузки УП из базы:', error);
        setPersistedPlanStatus(error.message || 'Ошибка загрузки УП', true);
        window.toast?.error(`Ошибка загрузки УП из базы: ${error.message}`);
    }
}

window.directions = curriculumDirections;
window.thematicBlocks = curriculumDirections;
Object.assign(window, {
    getCurrentCurriculumContext,
    handleCurriculumUpload,
    populateCurriculumBlocks,
    onCurriculumBlockChange,
    onCurriculumProjectChange,
    buildCurriculumContext,
    onDirectionChange,
    restoreCurriculumFromSession,
    loadThematicBlocks,
    loadPersistedCurriculumPlans,
    loadPersistedCurriculumPlan,
    updateDirectionSelect,
    updateThematicBlockSelect,
    addThematicBlock
});
