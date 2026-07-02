// Generation polling, timer and active-run controls.
// The module talks to main.js only through ContentGenGenerationRuntime.

let generationTimerHandle = null;
let agentPollIntervalHandle = null;
let statusPollIntervalHandle = null;
const RECOVERABLE_STATUS_ERRORS = new Set([404, 502, 503, 504]);

function getGenerationPollingRuntime() {
    return window.ContentGenGenerationRuntime || {};
}

function getGenerationPollingState() {
    const runtime = getGenerationPollingRuntime();
    return typeof runtime.getState === 'function' ? runtime.getState() : {};
}

function setGenerationPollingState(updates) {
    const runtime = getGenerationPollingRuntime();
    if (typeof runtime.setState === 'function') {
        runtime.setState(updates || {});
    }
}

function getPollingApiUrl() {
    const runtime = getGenerationPollingRuntime();
    if (typeof runtime.getApiUrl === 'function') return runtime.getApiUrl();
    return window.ContentGenApiUrl || window.API_URL || `${window.location.origin}/api/v1`;
}

function getPollingAuthHeaders() {
    const runtime = getGenerationPollingRuntime();
    if (typeof runtime.getAuthHeaders === 'function') return runtime.getAuthHeaders();
    if (typeof window.getAuthHeaders === 'function') return window.getAuthHeaders();
    const token = localStorage.getItem('auth_token');
    return {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json'
    };
}

function setGenerateButton(disabled, text = 'Сгенерировать') {
    const button = document.getElementById('generateBtn');
    if (!button) return;
    button.disabled = !!disabled;
    button.textContent = text;
}

function setLogContent(message, kind = 'error') {
    const logContent = document.getElementById('logContent');
    if (!logContent) return;
    if (window.sanitize && kind === 'error') {
        window.sanitize.safeSetErrorMessage(logContent, message);
        return;
    }
    if (window.sanitize && kind === 'html') {
        window.sanitize.safeSetHTML(logContent, message);
        return;
    }
    logContent.textContent = message;
}

function formatTime(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

function updateTimer() {
    const state = getGenerationPollingState();
    if (!state.generationStartTime) return;
    const elapsed = Math.floor((Date.now() - state.generationStartTime) / 1000);
    const value = formatTime(elapsed);
    const timerElement = document.getElementById('generationTimer');
    if (timerElement) timerElement.textContent = value;
    const runTimerElement = document.getElementById('generationRunTimer');
    if (runTimerElement) runTimerElement.textContent = value;
}

function startTimer() {
    if (generationTimerHandle) {
        clearInterval(generationTimerHandle);
    }

    let state = getGenerationPollingState();
    if (!state.generationStartTime) {
        try {
            const saved = sessionStorage.getItem('generation_state');
            const savedState = saved ? JSON.parse(saved) : null;
            if (savedState?.generationStartTime) {
                setGenerationPollingState({ generationStartTime: savedState.generationStartTime });
            }
        } catch (error) {
            console.debug('Не удалось восстановить время начала генерации:', error);
        }
    }

    state = getGenerationPollingState();
    if (!state.generationStartTime) {
        setGenerationPollingState({ generationStartTime: Date.now() });
    }

    let lastUpdate = Date.now();
    generationTimerHandle = setInterval(() => {
        const now = Date.now();
        if (now - lastUpdate >= 1000) {
            updateTimer();
            lastUpdate = now;
        }
        requestAnimationFrame(() => {
            if (getGenerationPollingState().generationStartTime) {
                updateTimer();
            }
        });
    }, 100);
}

function setGenerationStatusActive(active) {
    const generationLogs = document.getElementById('generationLogs');
    if (generationLogs) {
        generationLogs.classList.toggle('is-active', !!active);
    }
}

async function updateCurrentAgent(requestId, options = {}) {
    if (!requestId) return;

    let state = getGenerationPollingState();
    const workflowOptions = window.workflowUiOptions?.({
        workflow: options.workflow,
        methodology: options.methodology || null,
        status: options.status || state.currentGenerationStatus
    }) || {};

    if (workflowOptions.workflow) {
        const currentPhase = workflowOptions.phase || state.lastKnownGenerationPhase || 'initialization';
        const progress = Math.max(workflowOptions.progress || 0, state.lastKnownGenerationProgress || 0, 1);
        const currentAgent = workflowOptions.agent || state.lastKnownGenerationAgent || 'Генерация проекта';
        setGenerationPollingState({
            lastKnownGenerationPhase: currentPhase,
            lastKnownGenerationProgress: progress,
            lastKnownGenerationAgent: currentAgent
        });
        renderCurrentAgent(currentAgent, currentPhase, progress, options);
        return;
    }

    try {
        const response = await fetch(`${getPollingApiUrl()}/metrics/${requestId}`, {
            headers: getPollingAuthHeaders()
        });
        if (!response.ok) return;

        const data = await response.json();
        const logs = data.logs || [];
        let currentAgent = 'Инициализация...';
        let currentPhase = null;
        let progress = 0;
        let sawMethodologyReview = false;

        for (let i = logs.length - 1; i >= 0; i -= 1) {
            const log = logs[i];
            if (!log.phase) continue;
            const phase = log.phase;
            if (phase === 'methodology_review') {
                sawMethodologyReview = true;
                continue;
            }
            currentPhase = phase;
            progress = window.calculateProgressFromPhase?.(phase) || 0;
            currentAgent = agentLabelFromPhase(phase, log.message || '');
            break;
        }

        state = getGenerationPollingState();
        const checkpointProgress = window.progressFromCheckpointStage?.(
            options.methodology?.checkpoint?.stage || options.methodology?.checkpoint?.id
        ) || 0;
        const phaseProgress = progress;
        progress = Math.max(progress, checkpointProgress, state.lastKnownGenerationProgress || 0);

        if (currentPhase) {
            const movedBack = (state.lastKnownGenerationProgress || 0) > phaseProgress
                && phaseProgress > 0
                && options.status !== 'completed';
            const updates = { lastKnownGenerationProgress: progress };
            if (movedBack && state.lastKnownGenerationAgent) {
                currentAgent = state.lastKnownGenerationAgent;
            } else {
                updates.lastKnownGenerationPhase = currentPhase;
                updates.lastKnownGenerationAgent = currentAgent;
            }
            setGenerationPollingState(updates);
        } else if (sawMethodologyReview) {
            currentAgent = options.status === 'needs_review'
                ? 'Ожидание методолога'
                : (state.lastKnownGenerationAgent || 'Продолжение генерации');
            progress = Math.max(progress, state.lastKnownGenerationProgress || checkpointProgress || 0);
        }

        renderCurrentAgent(currentAgent, currentPhase || state.lastKnownGenerationPhase, progress, options);
    } catch (error) {
        console.debug('Не удалось получить логи:', error);
    }
}

function agentLabelFromPhase(phase, message = '') {
    const phaseMap = {
        initialization: 'Инициализация',
        context: 'Контекст проекта',
        task_planning: 'Планирование задач',
        title: 'Название',
        title_annotation: 'Название и аннотация',
        skeleton: 'Агент каркаса',
        intro_rules: 'Введение и инструкция',
        structural_preflight: 'Проверка структуры',
        theory: 'Теоретический агент',
        definitions: 'Определения',
        theory_checks: 'Проверка теории',
        practice: 'Практический агент',
        dataset_generation: 'Материалы практики',
        quality: 'Агент качества',
        global_quality: 'Агент качества',
        translate: 'Агент перевода',
        evaluation: 'Агент оценки',
        finalize: 'Финальная обработка',
        readme_check: 'Проверка README',
        completion: 'Завершение',
        validation: 'Валидация',
        validation_error: 'Ошибка валидации',
        generation_error: 'Ошибка генерации',
        unexpected_error: 'Неожиданная ошибка',
        methodology_review: 'Ожидание методолога'
    };
    if (phaseMap[phase]) return phaseMap[phase];
    if (message.includes('агент') || message.includes('Agent')) {
        const agentMatch = message.match(/(\w+Agent|\w+ агент)/i);
        if (agentMatch) return agentMatch[1];
    }
    return phase;
}

function renderCurrentAgent(agent, phase, progress, options = {}) {
    const agentElement = document.getElementById('currentAgent');
    if (agentElement) agentElement.textContent = agent;
    window.updateGenerationTimeline?.(phase, options.status || 'in_progress');
    if (window.loading && window.currentProgressBarId && progress > 0) {
        window.loading.updateProgress(window.currentProgressBarId, progress);
    }
    window.updateGenerationRunProgress?.(phase || 'initialization', options.status || getGenerationPollingState().currentGenerationStatus || 'in_progress', {
        progress,
        agent,
        methodology: options.methodology || null
    });
}

function stopGenerationTracking(options = {}) {
    if (generationTimerHandle) {
        clearInterval(generationTimerHandle);
        generationTimerHandle = null;
    }
    if (agentPollIntervalHandle) {
        clearInterval(agentPollIntervalHandle);
        agentPollIntervalHandle = null;
    }
    if (statusPollIntervalHandle) {
        clearInterval(statusPollIntervalHandle);
        statusPollIntervalHandle = null;
    }
    if (!options.preserveStartTime) {
        setGenerationPollingState({ generationStartTime: null });
    }
}

function hideCancelButton() {
    const cancelBtn = document.getElementById('cancelGenerationBtn');
    if (!cancelBtn) return;
    cancelBtn.style.display = 'none';
    cancelBtn.disabled = false;
    cancelBtn.textContent = 'Аварийная остановка генерации';
}

function showCancelButton() {
    const cancelBtn = document.getElementById('cancelGenerationBtn');
    if (!cancelBtn) {
        console.error('❌ Кнопка cancelGenerationBtn не найдена в DOM!');
        return;
    }
    cancelBtn.style.setProperty('display', 'block', 'important');
    cancelBtn.style.setProperty('visibility', 'visible', 'important');
    cancelBtn.style.setProperty('opacity', '1', 'important');
    cancelBtn.style.setProperty('width', '100%', 'important');
    cancelBtn.disabled = false;
    cancelBtn.textContent = 'Аварийная остановка генерации';
}

async function pollGenerationStatus(requestId) {
    if (!requestId) return;
    if (statusPollIntervalHandle) {
        clearInterval(statusPollIntervalHandle);
    }

    statusPollIntervalHandle = setInterval(async () => {
        try {
            const response = await fetch(`${getPollingApiUrl()}/generate/status/${requestId}`, {
                headers: getPollingAuthHeaders()
            });

            if (!response.ok) {
                await handleStatusHttpError(response);
                return;
            }
            const data = await parseStatusResponse(response);
            if (!data) return;

            const status = data.status;
            if (!status) {
                console.error('❌ Отсутствует поле status в ответе:', data);
                return;
            }

            setGenerationPollingState({
                currentGenerationStatus: status,
                workflowProfile: data.workflow_profile || undefined
            });
            const workflowMeta = window.workflowUiOptions?.(data) || {};
            console.debug('Polling status:', { status, hasResult: !!data.result, requestId, workflow: !!data.workflow });

            if (data.methodology) {
                window.renderMethodologyPanel?.(data.methodology, 'methodologyLiveStatus', { compact: true });
            }

            await updateCurrentAgent(requestId, {
                status,
                methodology: data.methodology || null,
                workflow: data.workflow || null
            });

            if (status === 'pending' || status === 'in_progress') {
                renderActiveGeneration(data, workflowMeta);
                return;
            }
            if (status === 'cancelled') {
                handleCancelledGeneration();
                return;
            }
            if (status === 'needs_review') {
                handleNeedsReviewGeneration(requestId, data, workflowMeta);
                return;
            }
            if (status === 'completed') {
                handleCompletedGeneration(data);
                return;
            }
            if (status === 'failed') {
                handleFailedGeneration(data);
            }
        } catch (error) {
            console.error('Ошибка при проверке статуса генерации:', error);
        }
    }, 1500);
}

async function handleStatusHttpError(response) {
    if (RECOVERABLE_STATUS_ERRORS.has(response.status)) {
        const state = getGenerationPollingState();
        const message = response.status === 404
            ? 'Сервер временно не нашёл запуск (404). Локальное состояние сохранено, продолжаю проверять статус.'
            : `Сервер временно недоступен (${response.status}). Локальное состояние сохранено, продолжаю проверять статус.`;
        setGenerationPollingState({
            currentGenerationStatus: state.currentGenerationStatus || 'in_progress',
            lastGenerationError: message
        });
        window.saveGenerationState?.();
        setLogContent(message, 'info');
        return;
    }

    let errorText = `Ошибка ${response.status}`;
    try {
        const errorData = await response.json();
        errorText = errorData.detail || errorData.message || errorText;
    } catch {
        try {
            errorText = await response.text();
        } catch {
            // Keep fallback text.
        }
    }
    console.error('Ошибка при проверке статуса:', errorText);
    setLogContent(`Ошибка сервера: ${errorText}`);
}

async function parseStatusResponse(response) {
    try {
        const contentType = response.headers.get('content-type');
        if (!contentType || !contentType.includes('application/json')) {
            console.error('⚠️ Сервер вернул не JSON ответ:', contentType);
            const text = await response.text();
            console.error('Ответ сервера:', text.substring(0, 200));
            setLogContent('Сервер вернул неожиданный формат ответа. Проверьте консоль браузера.');
            return null;
        }
        const data = await response.json();
        if (!data || typeof data !== 'object') {
            console.error('❌ Неожиданная структура ответа:', data);
            setLogContent('Сервер вернул неожиданный ответ. Проверьте консоль браузера.');
            return null;
        }
        return data;
    } catch (parseError) {
        console.error('❌ Ошибка парсинга JSON ответа:', parseError);
        setLogContent('Ошибка парсинга ответа сервера. Проверьте консоль браузера.');
        return null;
    }
}

function renderActiveGeneration(_data, workflowMeta) {
    const state = getGenerationPollingState();
    window.showGenerationRunView?.(state.currentSeed || {}, {
        phase: workflowMeta.phase || state.lastKnownGenerationPhase || 'initialization',
        status: state.currentGenerationStatus || 'in_progress',
        progress: Math.max(workflowMeta.progress || 0, state.lastKnownGenerationProgress || 0, 1),
        agent: workflowMeta.agent || state.lastKnownGenerationAgent || 'Генерация проекта'
    });
    setGenerationStatusActive(true);
    const cancelBtn = document.getElementById('cancelGenerationBtn');
    if (cancelBtn) {
        cancelBtn.style.setProperty('display', 'block', 'important');
        cancelBtn.disabled = false;
    }
}

function handleCancelledGeneration() {
    stopGenerationTracking();
    setLogContent('<div class="warning-msg">Генерация остановлена пользователем</div>', 'html');
    setGenerationStatusActive(false);
    setGenerateButton(false);
    hideCancelButton();
    window.hideMethodologyReviewActions?.();
    window.finishGenerationRun?.('cancelled');
}

function handleNeedsReviewGeneration(requestId, data, workflowMeta) {
    stopGenerationTracking({ preserveStartTime: true });
    hideCancelButton();
    setGenerationStatusActive(false);

    const errorMsg = data.error || 'Требуется ручная методологическая проверка';
    const escaped = window.sanitize ? window.sanitize.escapeHtml(errorMsg) : errorMsg;
    const logContent = document.getElementById('logContent');
    if (logContent) logContent.innerHTML = `<div class="warning-msg">${escaped}</div>`;

    if (data.methodology) {
        window.renderMethodologyPanel?.(data.methodology, 'methodologyLiveStatus', { compact: true });
    }

    const state = getGenerationPollingState();
    window.showGenerationRunView?.(state.currentSeed || {}, {
        phase: workflowMeta.phase || data.methodology?.checkpoint?.stage || state.lastKnownGenerationPhase || 'methodology_review',
        status: 'needs_review',
        methodology: data.methodology || null,
        progress: Math.max(
            workflowMeta.progress || 0,
            state.lastKnownGenerationProgress || 0,
            window.progressFromCheckpointStage?.(data.methodology?.checkpoint?.stage) || 0,
            1
        ),
        message: errorMsg,
        agent: workflowMeta.agent || 'Ожидание методолога'
    });

    const runtime = getGenerationPollingRuntime();
    runtime.appendAssistantChatMessage?.('assistant', 'Пайплайн остановлен на контрольной точке. Напишите правку в чат, и я отправлю её как запрос методолога.');
    window.showMethodologyReviewActions?.(requestId, errorMsg);
    const noResults = document.getElementById('noResults');
    if (noResults) noResults.style.display = 'none';
    document.getElementById('generationRunView')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setGenerateButton(false);
}

function handleCompletedGeneration(data) {
    if (!data.result) {
        console.warn('⚠️ Статус completed, но результат отсутствует. Продолжаем polling...', { requestId: data.request_id });
        return;
    }

    stopGenerationTracking();
    hideCancelButton();
    window.hideMethodologyReviewActions?.();

    if (!validateGenerationResult(data.result)) {
        setGenerateButton(false);
        return;
    }

    console.log('✅ Результат получен:', {
        hasMarkdown: !!data.result.markdown,
        hasRubric: !!data.result.rubric,
        keys: Object.keys(data.result || {})
    });

    setGenerationPollingState({
        currentRequestId: data.request_id,
        currentResult: data.result,
        currentMarkdown: data.result.markdown,
        originalMarkdown: data.result.markdown,
        originalRubric: data.result?.rubric || undefined,
        originalTextStats: data.result?.text_stats || undefined,
        workflowProfile: data.workflow_profile || data.result.workflow_profile || undefined
    });
    window.saveGenerationState?.();

    if (window.loading) {
        if (window.currentSpinnerId) window.loading.hideSpinner(window.currentSpinnerId);
        if (window.currentProgressBarId) window.loading.removeProgressBar(window.currentProgressBarId);
    }

    const logContent = document.getElementById('logContent');
    if (logContent) logContent.innerHTML = '';
    const generationLogs = document.getElementById('generationLogs');
    if (generationLogs) generationLogs.style.display = 'none';
    setGenerationStatusActive(false);
    window.finishGenerationRun?.('completed');
    window.toast?.success('Генерация успешно завершена!');

    try {
        window.displayResults?.({
            request_id: data.request_id,
            result: data.result,
            warnings: data.warnings || [],
            methodology: data.methodology || data.result.methodology_gate || null,
            workflow_profile: data.workflow_profile || data.result.workflow_profile || null
        });
    } catch (displayError) {
        console.error('❌ Ошибка при отображении результатов:', displayError);
        setLogContent(`Ошибка отображения результатов: ${displayError.message}. Проверьте консоль браузера (F12).`);
        if (generationLogs) generationLogs.style.display = 'block';
        setGenerationStatusActive(false);
    }

    const button = document.getElementById('generateBtn');
    if (button && window.loading) {
        window.loading.setButtonLoading(button, false, 'Сгенерировать');
    } else {
        setGenerateButton(false);
    }
}

function validateGenerationResult(result) {
    if (!result || typeof result !== 'object') {
        console.error('❌ Результат имеет неожиданную структуру:', result);
        setLogContent('Результат генерации имеет неожиданную структуру. Проверьте консоль браузера.');
        return false;
    }
    if (typeof result.markdown !== 'string') {
        console.error('❌ Отсутствует или неверный тип поля markdown:', typeof result.markdown, result);
        setLogContent('Результат генерации не содержит markdown. Проверьте консоль браузера.');
        return false;
    }
    return true;
}

function handleFailedGeneration(data) {
    const message = `Ошибка генерации: ${data.error || 'Неизвестная ошибка'}`;
    setGenerationPollingState({
        currentGenerationStatus: 'failed',
        lastGenerationError: message,
    });
    window.saveGenerationState?.();
    stopGenerationTracking();
    setGenerationStatusActive(false);
    window.finishGenerationRun?.('failed', data.error ? `Генерация остановилась с ошибкой: ${data.error}` : '');
    hideCancelButton();
    setLogContent(message);
    setGenerateButton(false);
}

Object.assign(window, {
    formatTime,
    updateTimer,
    startTimer,
    setGenerationStatusActive,
    updateCurrentAgent,
    stopGenerationTracking,
    hideCancelButton,
    showCancelButton,
    pollGenerationStatus
});
