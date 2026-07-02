// README improvement generation run/progress state.

let improvementTimerInterval = null;
let improvementStartTime = null;

function checkerRunApiUrl() {
    return window.API_URL || (window.API_BASE ? `${window.API_BASE}/api/v1` : '/api/v1');
}

function checkerInputValue(id) {
    return document.getElementById(id)?.value || '';
}

function checkerCheckboxValue(id) {
    return !!document.getElementById(id)?.checked;
}

function splitLines(value) {
    return (value || '').split('\n').map(item => item.trim()).filter(Boolean);
}

function splitCsv(value) {
    return (value || '').split(',').map(item => item.trim()).filter(Boolean);
}

async function generateImprovedReadme() {
    const improvementRequestId = window.getCheckerImprovementRequestId?.();
    if (!improvementRequestId) {
        alert('Ошибка: request_id не найден. Пожалуйста, начните процесс заново.');
        return;
    }

    const seedData = buildImprovementSeedData();
    if (!seedData.title_seed || !seedData.project_description || !seedData.thematic_block) {
        alert('Пожалуйста, заполните все обязательные поля (отмечены *)');
        return;
    }

    let runViewStarted = false;
    try {
        const authHeaders = window.getAuthHeadersForImprovement?.() || {};
        if (!authHeaders['Authorization']) {
            alert('Ошибка: требуется авторизация. Пожалуйста, войдите в систему.');
            return;
        }

        window.closeImprovementModal?.();
        showCheckerImprovementRunView(seedData);
        runViewStarted = true;

        const response = await fetch(`${checkerRunApiUrl()}/readme/improve/generate`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...authHeaders
            },
            body: JSON.stringify({
                request_id: improvementRequestId,
                seed: seedData
            })
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Ошибка генерации' }));
            throw new Error(error.detail || `Ошибка ${response.status}`);
        }

        const data = await response.json();
        window.setCheckerImprovementGenerationRequestId?.(data.generation_request_id);
        showImprovementProgress();
        startImprovementTimer();
        checkGenerationStatus();
    } catch (error) {
        console.error('Ошибка генерации:', error);
        if (runViewStarted) {
            finishCheckerImprovementRun();
        }
        alert(`Ошибка при запуске генерации: ${error.message}`);
    }
}

function buildImprovementSeedData() {
    const seedData = {
        language: checkerInputValue('improveLanguage'),
        llm_provider: window.getSelectedLlmProvider?.() || 'polza',
        project_type: checkerInputValue('improveProjectType'),
        thematic_block: checkerInputValue('improveThematicBlock'),
        audience_level: checkerInputValue('improveAudienceLevel'),
        title_seed: checkerInputValue('improveTitleSeed'),
        project_description: checkerInputValue('improveDescription'),
        learning_outcomes: splitLines(checkerInputValue('improveLearningOutcomes')),
        skills: splitCsv(checkerInputValue('improveSkills')),
        required_tools: splitCsv(checkerInputValue('improveRequiredTools')),
        tasks_count: checkerInputValue('improveTasksCount') ? parseInt(checkerInputValue('improveTasksCount'), 10) : null,
        methodology_human_review: checkerCheckboxValue('improveMethodologyHumanReview')
    };

    const curriculumContext = window.getCheckerCurriculumContext?.();
    if (curriculumContext) {
        seedData.curriculum_context = curriculumContext;
    }

    if (seedData.project_type === 'group') {
        const groupSizeValue = checkerInputValue('improveGroupSize');
        if (groupSizeValue) {
            seedData.group_size = parseInt(groupSizeValue, 10);
        }
    }

    const zunValue = checkerInputValue('improveZUN').trim();
    if (zunValue) {
        seedData.zun = zunValue;
    }

    if (checkerCheckboxValue('improveGenerateBonus')) {
        seedData.bonus_wish = checkerInputValue('improveBonusWish').trim() || '';
    } else {
        seedData.bonus_wish = null;
    }

    const repoBaseUrl = checkerInputValue('improveRepoBaseUrl').trim();
    if (repoBaseUrl) {
        seedData.repo_base_url = repoBaseUrl;
    }

    const repoPathTemplate = checkerInputValue('improveRepoPathTemplate').trim();
    if (repoPathTemplate) {
        seedData.repo_path_template = repoPathTemplate;
    }

    return seedData;
}

function showImprovementProgress() {
    const progressElement = document.getElementById('improvementGenerationProgress');
    if (progressElement) {
        progressElement.style.display = 'block';
    }
    const agentElement = document.getElementById('improvementCurrentAgent');
    if (agentElement) {
        agentElement.textContent = 'Инициализация...';
    }
}

function hideImprovementProgress() {
    const progressElement = document.getElementById('improvementGenerationProgress');
    if (progressElement) {
        progressElement.style.display = 'none';
    }
}

function startImprovementTimer() {
    improvementStartTime = Date.now();
    if (improvementTimerInterval) {
        clearInterval(improvementTimerInterval);
    }

    improvementTimerInterval = setInterval(() => {
        if (!improvementStartTime) return;

        const elapsed = Math.floor((Date.now() - improvementStartTime) / 1000);
        const minutes = Math.floor(elapsed / 60);
        const seconds = elapsed % 60;
        const formatted = `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;

        const timerElement = document.getElementById('improvementTimer');
        if (timerElement) timerElement.textContent = formatted;

        const runTimerElement = document.getElementById('checkerRunTimer');
        if (runTimerElement) runTimerElement.textContent = formatted;
    }, 1000);
}

function stopImprovementTimer() {
    if (improvementTimerInterval) {
        clearInterval(improvementTimerInterval);
        improvementTimerInterval = null;
    }
    improvementStartTime = null;
}

function setCheckerText(id, value) {
    const element = document.getElementById(id);
    if (element) element.textContent = value || '—';
}

function showCheckerImprovementRunView(seedData) {
    document.body.classList.add('checker-generating-assignment');

    const fileName = document.getElementById('checkerReadmeUploadTitle')?.textContent || 'README.md';
    const type = seedData.project_type === 'group'
        ? `Групповой${seedData.group_size ? ' · ' + seedData.group_size + ' чел.' : ''}`
        : 'Индивидуальный';

    setCheckerText('checkerRunParamReadme', fileName);
    setCheckerText('checkerRunParamTitle', seedData.title_seed || '—');
    setCheckerText('checkerRunParamBlock', seedData.thematic_block || '—');
    setCheckerText('checkerRunParamType', type);
    setCheckerText('checkerRunParamTasks', seedData.tasks_count ? String(seedData.tasks_count) : 'авто');
    setCheckerText('checkerRunParamMethodology', seedData.methodology_human_review ? 'Включена' : 'Обычный режим');
    setCheckerText('checkerBrandMark', 'ПРОГРЕСС УЛУЧШЕНИЯ');
    setCheckerText('checkerBrandSub', 'генерация улучшенного README');

    const badge = document.getElementById('checkerBrandBadge');
    if (badge) badge.setAttribute('data-step', '04.2');

    const status = document.getElementById('checkerSubbarStatus');
    if (status) {
        status.style.display = 'inline-flex';
        status.className = 'badge info';
        status.textContent = 'ВЫПОЛНЯЕТСЯ';
    }

    setCheckerText('checkerSubbarTitle', 'Генерация улучшенного README');
}

function finishCheckerImprovementRun() {
    document.body.classList.remove('checker-generating-assignment');

    const runView = document.getElementById('checkerImprovementRunView');
    const runSnapshot = document.getElementById('checkerRunSnapshot');
    const inputForm = document.getElementById('checkerInputForm');
    const resultsArea = document.getElementById('resultsArea');

    if (runView) runView.style.display = 'none';
    if (runSnapshot) runSnapshot.style.display = 'none';
    if (inputForm) inputForm.style.display = 'block';
    if (resultsArea) resultsArea.style.display = 'block';

    setCheckerText('checkerBrandMark', 'ПРОВЕРКА');
    const badge = document.getElementById('checkerBrandBadge');
    if (badge) badge.setAttribute('data-step', '04.1');
}

async function updateImprovementCurrentAgent(requestId) {
    if (!requestId) return;

    try {
        const authHeaders = window.getAuthHeadersForImprovement?.() || {};
        const response = await fetch(`${checkerRunApiUrl()}/metrics/${requestId}`, {
            headers: authHeaders
        });

        if (!response.ok) return;

        const data = await response.json();
        const currentAgent = resolveCurrentAgent(data.logs || []);
        const agentElement = document.getElementById('improvementCurrentAgent');
        if (agentElement) {
            agentElement.textContent = currentAgent;
        }
    } catch (error) {
        console.debug('Не удалось получить логи для улучшения:', error);
    }
}

function resolveCurrentAgent(logs) {
    const phaseMap = {
        'structure_extraction': 'Извлечение структуры',
        'classification': 'Классификация метаданных',
        'task_planning': 'Планирование задач',
        'task_generation': 'Генерация задач',
        'readme_generation': 'Генерация README',
        'validation': 'Валидация',
        'finalization': 'Финальная обработка'
    };

    for (let index = logs.length - 1; index >= 0; index--) {
        const log = logs[index];
        if (!log.phase) continue;

        const phase = log.phase;
        const message = log.message || '';
        if (phaseMap[phase]) return phaseMap[phase];

        if (message.includes('агент') || message.includes('Agent')) {
            const agentMatch = message.match(/(\w+Agent|\w+ агент)/i);
            return agentMatch ? agentMatch[1] : phase;
        }
        return phase;
    }

    return 'Инициализация...';
}

async function checkGenerationStatus() {
    const generationRequestId = window.getCheckerImprovementGenerationRequestId?.();
    if (!generationRequestId) return;

    try {
        const authHeaders = window.getAuthHeadersForImprovement?.() || {};
        await updateImprovementCurrentAgent(generationRequestId);

        const response = await fetch(`${checkerRunApiUrl()}/readme/improve/status/${generationRequestId}`, {
            headers: authHeaders
        });

        if (!response.ok) {
            console.error('Ошибка проверки статуса:', response.status);
            setTimeout(checkGenerationStatus, 5000);
            return;
        }

        const data = await response.json();
        if (data.status === 'completed' && data.result) {
            await handleImprovementCompleted(data.result);
        } else if (data.status === 'failed') {
            handleImprovementFailed();
        } else {
            setTimeout(checkGenerationStatus, 5000);
        }
    } catch (error) {
        console.error('Ошибка проверки статуса:', error);
        setTimeout(checkGenerationStatus, 5000);
    }
}

async function handleImprovementCompleted(result) {
    stopImprovementTimer();
    finishCheckerImprovementRun();
    hideImprovementProgress();

    const improvedReadme = result.markdown || '';
    if (!improvedReadme) {
        alert('Генерация завершена, но улучшенный README не найден в результате.');
        return;
    }

    window.improvedReadme = improvedReadme;
    if (result.assets) {
        window.improvedReadmeAssets = result.assets;
    }
    persistImprovedRubric(result.rubric);

    const resultsArea = document.getElementById('resultsArea');
    if (!resultsArea) {
        alert('Ошибка: область результатов не найдена. Попробуйте обновить страницу.');
        return;
    }
    resultsArea.style.display = 'block';

    window.displayImprovedReadme?.(improvedReadme);
    setTimeout(async () => {
        await window.displayReadmeDiff?.();
    }, 500);

    alert('Генерация улучшенного README завершена. Проверьте вкладки "Улучшенный README" и "Сравнение".');
}

function persistImprovedRubric(rubric) {
    if (!rubric || typeof rubric !== 'object' || Array.isArray(rubric)) {
        console.warn('Критерии улучшенного README не найдены или имеют неверный формат');
        return;
    }
    if (!Array.isArray(rubric.items) || rubric.items.length === 0) {
        console.warn('Критерии улучшенного README невалидны: items отсутствует или пуст');
        return;
    }

    window.improvedRubric = rubric;
    setTimeout(() => {
        const tabImproved = document.getElementById('checkerMetricsTabImproved');
        if (tabImproved) {
            tabImproved.classList.add('active');
            document.getElementById('checkerMetricsTabOriginal')?.classList.remove('active');
        }
        window.updateCheckerMetricsDisplay?.();
    }, 500);
}

function handleImprovementFailed() {
    stopImprovementTimer();
    finishCheckerImprovementRun();
    hideImprovementProgress();
    alert('Генерация завершилась с ошибкой. Проверьте логи.');
}

window.generateImprovedReadme = generateImprovedReadme;
window.buildImprovementSeedData = buildImprovementSeedData;
window.startImprovementTimer = startImprovementTimer;
window.stopImprovementTimer = stopImprovementTimer;
window.showCheckerImprovementRunView = showCheckerImprovementRunView;
window.finishCheckerImprovementRun = finishCheckerImprovementRun;
window.updateImprovementCurrentAgent = updateImprovementCurrentAgent;
window.checkGenerationStatus = checkGenerationStatus;


