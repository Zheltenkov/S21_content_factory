// Floating methodology assistant chat for generation runtime.
// The module owns DOM events and review-change submission; main.js only passes runtime state.

(function () {
    let config = {};
    let flushInProgress = false;
    let initialized = false;
    let resizeObserver = null;
    let resizeSyncing = false;
    let latestReviewState = null;
    let reviewControlsBusy = false;
    const CHAT_BOUNDS_KEY = 'methodology_assistant_chat_bounds_v2';

    function configure(options = {}) {
        config = { ...config, ...options };
    }

    function getState() {
        if (typeof config.getState === 'function') {
            return config.getState() || {};
        }
        if (window.ContentGenGenerationRuntime?.getState) {
            return window.ContentGenGenerationRuntime.getState() || {};
        }
        return {};
    }

    function getApiUrl() {
        if (typeof config.getApiUrl === 'function') {
            return config.getApiUrl();
        }
        return window.ContentGenApiUrl || window.API_URL || `${window.location.origin}/api/v1`;
    }

    function getAuthHeaders() {
        if (typeof config.getAuthHeaders === 'function') {
            return config.getAuthHeaders();
        }
        if (typeof window.getAuthHeaders === 'function') {
            return window.getAuthHeaders();
        }
        const token = localStorage.getItem('auth_token');
        return token
            ? { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }
            : { 'Content-Type': 'application/json' };
    }

    function escapeHtml(value) {
        if (typeof window.escapeHtmlSafe === 'function') {
            return window.escapeHtmlSafe(value);
        }
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function currentStageId() {
        const state = getState();
        if (typeof window.runStageFromPhase === 'function') {
            return window.runStageFromPhase(state.lastKnownGenerationPhase);
        }
        return state.lastKnownGenerationPhase || 'pipeline';
    }

    function currentRequestId() {
        return getState().currentRequestId || '';
    }

    function pendingKey(requestId = currentRequestId()) {
        return requestId ? `methodology_assistant_pending:${requestId}` : '';
    }

    function isMethodologyMode() {
        if (typeof config.isEnabled === 'function') {
            return Boolean(config.isEnabled());
        }
        const state = getState();
        const capabilities = state.workflowCapabilities || state.workflowProfile?.capabilities || {};
        if (Object.prototype.hasOwnProperty.call(capabilities, 'methodology_assistant')) {
            return Boolean(capabilities.methodology_assistant);
        }
        if (state.currentSeed && Object.prototype.hasOwnProperty.call(state.currentSeed, 'methodology_human_review')) {
            return Boolean(state.currentSeed.methodology_human_review);
        }
        return Boolean(document.getElementById('methodologyHumanReview')?.checked);
    }

    function clamp(value, min, max) {
        if (!Number.isFinite(value)) return min;
        return Math.max(min, Math.min(max, value));
    }

    function applyChatBounds(chat, bounds = {}) {
        const viewportWidth = Math.max(window.innerWidth || 0, 360);
        const viewportHeight = Math.max(window.innerHeight || 0, 420);
        const maxWidth = Math.max(320, viewportWidth - 24);
        const maxHeight = Math.max(340, viewportHeight - 24);
        const width = clamp(Number(bounds.width) || chat.getBoundingClientRect().width || 474, 320, maxWidth);
        const height = clamp(Number(bounds.height) || chat.getBoundingClientRect().height || 560, 340, maxHeight);
        const left = clamp(Number(bounds.left) || viewportWidth - width - 30, 12, viewportWidth - width - 12);
        const top = clamp(Number(bounds.top) || viewportHeight - height - 30, 12, viewportHeight - height - 12);

        chat.style.left = `${Math.round(left)}px`;
        chat.style.top = `${Math.round(top)}px`;
        chat.style.right = 'auto';
        chat.style.bottom = 'auto';
        chat.style.width = `${Math.round(width)}px`;
        chat.style.height = `${Math.round(height)}px`;
    }

    function restoreChatBounds(chat) {
        try {
            const raw = localStorage.getItem(CHAT_BOUNDS_KEY);
            if (raw) {
                applyChatBounds(chat, JSON.parse(raw));
            }
        } catch (_error) {
            localStorage.removeItem(CHAT_BOUNDS_KEY);
        }
    }

    function saveChatBounds(chat) {
        if (!chat || chat.style.display === 'none') return;
        const rect = chat.getBoundingClientRect();
        if (!rect.width || !rect.height) return;
        const bounds = {
            left: Math.round(rect.left),
            top: Math.round(rect.top),
            width: Math.round(rect.width),
            height: Math.round(rect.height),
        };
        try {
            localStorage.setItem(CHAT_BOUNDS_KEY, JSON.stringify(bounds));
        } catch (_error) {
            // Размер окна чата не является критичным состоянием.
        }
    }

    function normalizeChatBounds(chat) {
        if (!chat || chat.style.display === 'none') return;
        const rect = chat.getBoundingClientRect();
        if (!rect.width || !rect.height) return;
        applyChatBounds(chat, {
            left: rect.left,
            top: rect.top,
            width: rect.width,
            height: rect.height,
        });
    }

    function makeChatDraggable(chat) {
        const handle = chat.querySelector('.assistant-chat-head');
        if (!handle || handle.dataset.dragReady === 'true') return;
        handle.dataset.dragReady = 'true';

        handle.addEventListener('pointerdown', (event) => {
            if (event.button !== 0 || event.target.closest('button, a, input, textarea, select')) {
                return;
            }
            const rect = chat.getBoundingClientRect();
            const offsetX = event.clientX - rect.left;
            const offsetY = event.clientY - rect.top;
            chat.classList.add('is-dragging');
            chat.style.width = `${Math.round(rect.width)}px`;
            chat.style.height = `${Math.round(rect.height)}px`;
            chat.style.right = 'auto';
            chat.style.bottom = 'auto';
            event.preventDefault();

            const onMove = (moveEvent) => {
                const width = chat.getBoundingClientRect().width;
                const height = chat.getBoundingClientRect().height;
                const left = clamp(moveEvent.clientX - offsetX, 12, window.innerWidth - width - 12);
                const top = clamp(moveEvent.clientY - offsetY, 12, window.innerHeight - height - 12);
                chat.style.left = `${Math.round(left)}px`;
                chat.style.top = `${Math.round(top)}px`;
            };

            const onUp = () => {
                document.removeEventListener('pointermove', onMove);
                document.removeEventListener('pointerup', onUp);
                chat.classList.remove('is-dragging');
                saveChatBounds(chat);
            };

            document.addEventListener('pointermove', onMove);
            document.addEventListener('pointerup', onUp, { once: true });
        });
    }

    function setupResizePersistence(chat) {
        if (resizeObserver || typeof ResizeObserver === 'undefined') return;
        resizeObserver = new ResizeObserver(() => {
            if (chat.classList.contains('is-dragging') || resizeSyncing) return;
            resizeSyncing = true;
            normalizeChatBounds(chat);
            saveChatBounds(chat);
            window.requestAnimationFrame(() => {
                resizeSyncing = false;
            });
        });
        resizeObserver.observe(chat);
        window.addEventListener('resize', () => {
            normalizeChatBounds(chat);
            saveChatBounds(chat);
        }, { passive: true });
    }

    function loadPendingCommands(requestId = currentRequestId()) {
        const key = pendingKey(requestId);
        if (!key) return [];
        try {
            const raw = sessionStorage.getItem(key);
            const parsed = raw ? JSON.parse(raw) : [];
            return Array.isArray(parsed) ? parsed.filter(item => item && item.text) : [];
        } catch (_error) {
            return [];
        }
    }

    function savePendingCommands(commands, requestId = currentRequestId()) {
        const key = pendingKey(requestId);
        if (!key) return;
        const safeCommands = Array.isArray(commands) ? commands.filter(item => item && item.text) : [];
        if (!safeCommands.length) {
            sessionStorage.removeItem(key);
            return;
        }
        sessionStorage.setItem(key, JSON.stringify(safeCommands.slice(-20)));
    }

    function queuePendingCommand(text, requestId = currentRequestId()) {
        const commands = loadPendingCommands(requestId);
        commands.push({
            text,
            queued_at: new Date().toISOString(),
            stage: currentStageId(),
        });
        savePendingCommands(commands, requestId);
    }

    function show(status = getState().currentGenerationStatus) {
        const chat = document.getElementById('methodologyAssistantChat');
        if (!chat) return;
        if (!isMethodologyMode()) {
            hide();
            return;
        }
        chat.style.display = 'grid';
        restoreChatBounds(chat);
        makeChatDraggable(chat);
        setupResizePersistence(chat);
        updateStatus(status, currentStageId());
    }

    function hide() {
        const chat = document.getElementById('methodologyAssistantChat');
        if (chat) {
            chat.style.display = 'none';
        }
    }

    function updateStatus(status = getState().currentGenerationStatus, stageId = currentStageId()) {
        if (!isMethodologyMode()) {
            hide();
            return;
        }
        const statusNode = document.getElementById('assistantChatStatus');
        const stageNode = document.getElementById('assistantChatStage');
        if (statusNode) {
            const labels = {
                pending: 'запуск ожидает',
                in_progress: 'генерация активна',
                needs_review: 'контрольная точка',
                completed: 'результат готов',
                failed: 'ошибка генерации',
                cancelled: 'остановлено'
            };
            statusNode.textContent = labels[status] || 'готов к комментариям';
        }
        if (stageNode) {
            stageNode.textContent = String(stageId || 'pipeline').toUpperCase();
        }
        if (status === 'needs_review') {
            refreshReviewControls(window.methodologyPanel?.getCurrentReviewState?.() || latestReviewState);
            flushPendingCommands();
        } else {
            refreshReviewControls(null);
        }
    }

    function appendMessage(role, text) {
        const messages = document.getElementById('assistantChatMessages');
        if (!messages || !text) return;
        const node = document.createElement('div');
        node.className = `assistant-message ${role === 'user' ? 'user' : 'assistant'}`;
        const avatar = role === 'user'
            ? (document.getElementById('generatorUserInitials')?.textContent || 'Вы')
            : 'М';
        node.innerHTML = `<span>${escapeHtml(avatar)}</span><div>${escapeHtml(text)}</div>`;
        messages.appendChild(node);
        messages.scrollTop = messages.scrollHeight;
    }

    function commandLabel(command) {
        return {
            approve: 'продолжить генерацию',
            request_changes: 'запросить правку',
            simplify_task: 'упростить задачу',
            add_example: 'добавить пример',
            fix_failed_criteria: 'исправить непройденные критерии',
            regenerate_section: 'перегенерировать раздел'
        }[command] || command || 'команда';
    }

    function normalizeStage(stage) {
        return {
            structure: 'skeleton',
            title_annotation: 'title',
        }[String(stage || '')] || String(stage || 'final');
    }

    function stageLabel(stage) {
        return {
            title: 'Название',
            annotation: 'Аннотация',
            skeleton: 'Структура',
            theory: 'Теория',
            practice: 'Практика',
            dataset: 'Материалы',
            final: 'Финальная сборка',
            task_planning: 'План задач',
            context: 'Контекст',
        }[stage] || stage || 'Этап';
    }

    function targetOptionLabel(target) {
        const label = target?.label || target?.id || 'текущий блок';
        const stage = stageLabel(target?.stage || '');
        return stage ? `${stage}: ${label}` : label;
    }

    function reviewTargets() {
        const fromPanel = window.methodologyPanel?.getTargetOptions?.();
        if (Array.isArray(fromPanel) && fromPanel.length) return fromPanel;
        return latestReviewState?.target_registry?.targets || [];
    }

    function selectedTargetId() {
        return document.getElementById('assistantChangeTarget')?.value || '';
    }

    function selectedTargetLabel(targetId = selectedTargetId()) {
        if (!targetId) return 'текущий блок';
        const target = reviewTargets().find(item => item.id === targetId);
        return target ? targetOptionLabel(target) : targetId;
    }

    function populateTargetPicker() {
        const select = document.getElementById('assistantChangeTarget');
        if (!select) return;
        const targets = reviewTargets();
        const signature = targets.map(target => `${target.id}:${target.label}:${target.stage}`).join('|');
        if (select.dataset.signature === signature) {
            renderTargetChips();
            return;
        }
        const current = select.value;
        const options = ['<option value="">Текущий блок</option>'].concat(
            targets.map(target => `<option value="${escapeHtml(target.id)}">${escapeHtml(targetOptionLabel(target))}</option>`)
        );
        select.innerHTML = options.join('');
        select.dataset.signature = signature;
        if (current && targets.some(target => target.id === current)) {
            select.value = current;
        } else {
            select.value = '';
        }
        renderTargetChips();
        updateChatInputPlaceholder();
    }

    function setSelectedTarget(targetId = '') {
        const select = document.getElementById('assistantChangeTarget');
        if (select) select.value = targetId;
        renderTargetChips();
        updateChatInputPlaceholder();
        document.getElementById('assistantChatInput')?.focus();
    }

    function renderTargetChips() {
        const chips = document.getElementById('assistantTargetChips');
        if (!chips) return;
        const targets = reviewTargets();
        const selected = selectedTargetId();
        const chipItems = [{ id: '', label: 'Текущий блок', stage: '' }].concat(targets);
        chips.innerHTML = chipItems.map(target => {
            const id = target.id || '';
            const label = id ? targetOptionLabel(target) : target.label;
            const active = selected === id ? ' is-active' : '';
            return `
                <button type="button" class="assistant-target-chip${active}" data-target-id="${escapeHtml(id)}" role="option" aria-selected="${active ? 'true' : 'false'}">
                    ${escapeHtml(label)}
                </button>
            `;
        }).join('');
        chips.querySelectorAll('.assistant-target-chip').forEach(button => {
            button.addEventListener('click', () => setSelectedTarget(button.getAttribute('data-target-id') || ''));
        });
    }

    function updateChatInputPlaceholder() {
        const input = document.getElementById('assistantChatInput');
        if (!input) return;
        const targetLabel = selectedTargetLabel();
        input.placeholder = `Правка для: ${targetLabel}. Напишите, что изменить...`;
    }

    function isReviewActive() {
        return getState().currentGenerationStatus === 'needs_review';
    }

    function setButtonState(id, disabled) {
        const button = document.getElementById(id);
        if (button) button.disabled = !!disabled;
    }

    function refreshReviewControls(state = latestReviewState, options = {}) {
        if (state === null) {
            latestReviewState = null;
        }
        if (state && typeof state === 'object') {
            latestReviewState = state;
        }
        reviewControlsBusy = Boolean(options.busy);
        const actions = document.getElementById('assistantChatActions');
        if (!actions) return;
        const visible = !options.forceHide && isMethodologyMode() && isReviewActive();
        actions.style.display = visible ? 'grid' : 'none';
        if (!visible) {
            const picker = document.getElementById('assistantTargetPicker');
            if (picker) picker.style.display = 'none';
            return;
        }
        const reviewState = latestReviewState || {};
        const pendingCount = Array.isArray(reviewState.pending_change_ids) ? reviewState.pending_change_ids.length : 0;
        const needsDiffApproval = !!reviewState.requires_diff_approval;
        const previewReady = reviewState.review_state === 'preview_ready';
        setButtonState('assistantActionContinue', reviewControlsBusy || needsDiffApproval);
        setButtonState('assistantActionEdit', reviewControlsBusy);
        setButtonState('assistantActionAccept', reviewControlsBusy || pendingCount === 0 || !previewReady || !!reviewState.preview_has_rejections);
        setButtonState('assistantActionCompare', reviewControlsBusy || pendingCount === 0);
        document.getElementById('assistantActionEdit')?.classList.toggle(
            'is-active',
            document.getElementById('assistantTargetPicker')?.style.display !== 'none'
        );
        populateTargetPicker();
    }

    function toggleTargetPicker(forceOpen = null) {
        const picker = document.getElementById('assistantTargetPicker');
        if (!picker) return;
        const shouldOpen = forceOpen === null ? picker.style.display === 'none' : !!forceOpen;
        picker.style.display = shouldOpen ? 'grid' : 'none';
        document.getElementById('assistantActionEdit')?.classList.toggle('is-active', shouldOpen);
        if (shouldOpen) {
            populateTargetPicker();
            document.getElementById('assistantChatInput')?.focus();
        }
    }

    async function runReviewAction(action) {
        if (action === 'edit') {
            toggleTargetPicker();
            return;
        }
        if (!isReviewActive()) {
            appendMessage('assistant', 'Сейчас нет активной контрольной точки.');
            return;
        }
        const panel = window.methodologyPanel;
        if (!panel) {
            appendMessage('assistant', 'Интерфейс ревью ещё не готов.');
            return;
        }
        try {
            reviewControlsBusy = true;
            refreshReviewControls();
            if (action === 'continue') {
                appendMessage('user', 'Продолжить генерацию');
                await panel.approveReview?.();
                appendMessage('assistant', 'Решение принято. Продолжаю пайплайн.');
                return;
            }
            if (action === 'compare') {
                appendMessage('user', 'Показать изменения');
                const responseData = await panel.previewChanges?.();
                latestReviewState = { ...(latestReviewState || {}), ...(responseData || {}) };
                refreshReviewControls(latestReviewState);
                appendMessage('assistant', responseData?.preview_has_rejections
                    ? 'Есть отклонённые правки. Посмотрите предупреждения в основном окне.'
                    : 'Сравнение готово в основном окне.');
                return;
            }
            if (action === 'accept') {
                appendMessage('user', 'Принять изменения');
                const responseData = await panel.approveDiff?.();
                latestReviewState = { ...(latestReviewState || {}), ...(responseData || {}) };
                refreshReviewControls(latestReviewState);
                appendMessage('assistant', 'Изменения приняты. Можно продолжить генерацию.');
            }
        } catch (error) {
            appendMessage('assistant', `Не удалось выполнить действие: ${error.message}`);
        } finally {
            reviewControlsBusy = false;
            refreshReviewControls();
        }
    }

    async function submitCommand(text, { appendUser = true, fromQueue = false } = {}) {
        const state = getState();
        if (!isMethodologyMode()) {
            hide();
            return;
        }
        show(state.currentGenerationStatus);
        if (appendUser) {
            const targetId = state.currentGenerationStatus === 'needs_review' ? selectedTargetId() : '';
            appendMessage('user', targetId ? `${selectedTargetLabel(targetId)}: ${text}` : text);
        }

        if (state.currentGenerationStatus === 'needs_review' && state.currentRequestId) {
            try {
                const response = await fetch(`${getApiUrl()}/generate/review/${state.currentRequestId}/assistant-command`, {
                    method: 'POST',
                    headers: getAuthHeaders(),
                    body: JSON.stringify({
                        message: text,
                        selected_target_id: document.getElementById('assistantChangeTarget')?.value || null
                    })
                });
                const responseData = await response.json().catch(() => ({}));
                if (!response.ok) {
                    const detail = responseData.detail || responseData || {};
                    throw new Error(detail.detail || detail.message || `Ошибка ${response.status}`);
                }
                const parsed = responseData.assistant_command || {};
                appendMessage('assistant', `${fromQueue ? 'Очередь применена. ' : ''}Распознано: ${commandLabel(parsed.command)}.`);
                if (responseData.status === 'in_progress' || ['approve', 'regenerate_section'].includes(parsed.command)) {
                    latestReviewState = null;
                    refreshReviewControls(null);
                    window.methodologyPanel?.hideActions?.();
                    if (typeof config.onApproved === 'function') {
                        config.onApproved(state.currentRequestId, text, responseData);
                    }
                    if (responseData.message) {
                        appendMessage('assistant', responseData.message);
                    }
                    return;
                }
                latestReviewState = {
                    ...(latestReviewState || {}),
                    ...responseData,
                };
                refreshReviewControls(latestReviewState);
                const message = parsed.command === 'regenerate_section'
                    ? 'Раздел отправлен на перегенерацию из durable checkpoint.'
                    : 'Команда из чата сохранена. Проверьте список правок и продолжайте генерацию, когда будете готовы.';
                if (typeof config.showMethodologyReviewActions === 'function') {
                    config.showMethodologyReviewActions(state.currentRequestId, message);
                } else if (typeof window.showMethodologyReviewActions === 'function') {
                    window.showMethodologyReviewActions(state.currentRequestId, message);
                }
                const panel = window.methodologyPanel;
                if (typeof panel?.previewChanges === 'function') {
                    try {
                        appendMessage('assistant', 'Готовлю сравнение, чтобы правка сразу была видна в основном окне.');
                        const previewData = await panel.previewChanges();
                        latestReviewState = {
                            ...(latestReviewState || {}),
                            ...(previewData || {}),
                        };
                        refreshReviewControls(latestReviewState);
                        appendMessage('assistant', previewData?.preview_has_rejections
                            ? 'Правка сохранена, но часть изменений отклонена валидатором. Посмотрите предупреждения в основном окне.'
                            : 'Правка готова в основном окне.');
                    } catch (previewError) {
                        appendMessage('assistant', `Правка сохранена, но предпросмотр не удалось подготовить: ${previewError.message}. Нажмите «Сравнить», чтобы повторить.`);
                    }
                }
            } catch (error) {
                if (fromQueue) {
                    queuePendingCommand(text, state.currentRequestId);
                }
                appendMessage('assistant', `Не удалось выполнить workflow-команду: ${error.message}.`);
            }
            return;
        }

        if (state.currentRequestId) {
            queuePendingCommand(text, state.currentRequestId);
            appendMessage('assistant', 'Команда поставлена в очередь. На ближайшей контрольной точке я отправлю ее как валидируемую workflow-команду.');
        } else {
            appendMessage('assistant', 'Нет активного запуска: команда не отправлена в workflow.');
        }
    }

    async function flushPendingCommands() {
        const requestId = currentRequestId();
        const state = getState();
        if (flushInProgress || !requestId || state.currentGenerationStatus !== 'needs_review') return;
        const commands = loadPendingCommands(requestId);
        if (!commands.length) return;
        flushInProgress = true;
        try {
            savePendingCommands([], requestId);
            appendMessage('assistant', `Отправляю pending-команд${commands.length === 1 ? 'у' : 'ы'} методолога в workflow.`);
            for (const item of commands) {
                await submitCommand(item.text, { appendUser: false, fromQueue: true });
            }
        } finally {
            flushInProgress = false;
        }
    }

    async function send(rawText = '') {
        const input = document.getElementById('assistantChatInput');
        const text = (rawText || input?.value || '').trim();
        if (!text) return;
        if (input) input.value = '';
        await submitCommand(text);
    }

    function initialize() {
        if (initialized) return;
        initialized = true;
        document.getElementById('assistantChatSend')?.addEventListener('click', () => send());
        document.getElementById('assistantChatClose')?.addEventListener('click', hide);
        document.getElementById('assistantActionContinue')?.addEventListener('click', () => runReviewAction('continue'));
        document.getElementById('assistantActionEdit')?.addEventListener('click', () => runReviewAction('edit'));
        document.getElementById('assistantActionAccept')?.addEventListener('click', () => runReviewAction('accept'));
        document.getElementById('assistantActionCompare')?.addEventListener('click', () => runReviewAction('compare'));
        document.getElementById('assistantChangeTarget')?.addEventListener('change', (event) => {
            setSelectedTarget(event.target?.value || '');
        });
        document.getElementById('methodologyHumanReview')?.addEventListener('change', () => {
            const status = getState().currentGenerationStatus;
            if (isMethodologyMode() && status && status !== 'idle') {
                show(status);
            } else {
                hide();
            }
        });
        document.getElementById('assistantChatInput')?.addEventListener('keydown', (event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                send();
            }
        });
        document.querySelectorAll('[data-assistant-suggestion]').forEach((button) => {
            button.addEventListener('click', () => {
                const input = document.getElementById('assistantChatInput');
                if (!input) return;
                input.value = button.getAttribute('data-assistant-suggestion') || '';
                input.focus();
            });
        });
    }

    window.MethodologyAssistantChat = {
        configure,
        isMethodologyMode,
        show,
        hide,
        updateStatus,
        appendMessage,
        send,
        flushPendingCommands,
        refreshReviewControls,
        initialize,
    };
})();
