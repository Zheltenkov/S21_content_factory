        window.addEventListener('error', function(e) {
            console.error('JavaScript ошибка:', e.error, e.message, e.filename, e.lineno);
            
            // Если ошибка связана с markdown, показываем её в UI
            if (e.message && (e.message.includes('markdown') || e.message.includes('Cannot read properties'))) {
                const logContent = document.getElementById('logContent');
                if (logContent) {
                    if (window.sanitize) {
                        window.sanitize.safeSetErrorMessage(logContent, `Ошибка: ${e.message}. Проверьте консоль браузера (F12) для подробностей.`);
                    } else {
                        logContent.textContent = `Ошибка: ${e.message}. Проверьте консоль браузера (F12) для подробностей.`;
                    }
                }
                const generationLogs = document.getElementById('generationLogs');
                if (generationLogs) {
                    generationLogs.style.display = 'block';
                }
            }
        });
        
        // Обработка необработанных промисов
        window.addEventListener('unhandledrejection', function(e) {
            console.error('Необработанная ошибка промиса:', e.reason);
            
            // Если ошибка связана с markdown, показываем её в UI
            const errorMsg = e.reason?.message || String(e.reason);
            if (errorMsg && (errorMsg.includes('markdown') || errorMsg.includes('Cannot read properties'))) {
                const logContent = document.getElementById('logContent');
                if (logContent) {
                    if (window.sanitize) {
                        window.sanitize.safeSetErrorMessage(logContent, `Ошибка: ${errorMsg}. Проверьте консоль браузера (F12) для подробностей.`);
                    } else {
                        logContent.textContent = `Ошибка: ${errorMsg}. Проверьте консоль браузера (F12) для подробностей.`;
                    }
                }
                const generationLogs = document.getElementById('generationLogs');
                if (generationLogs) {
                    generationLogs.style.display = 'block';
                }
            }
        });
        
        // Оборачиваем весь код в IIFE, чтобы можно было использовать return
        (function() {
            const API_BASE = window.location.origin;
            const API_URL = `${API_BASE}/api/v1`;
            window.API_URL = API_URL;
            window.ContentGenApiUrl = API_URL;
            const REGENERATION_TEMPLATE = 'Примени точечно правки к документу по пунктам ниже не нарушая его структуру и стиль.';
            
            // Проверка аутентификации
            const authToken = localStorage.getItem('auth_token');
            if (!authToken) {
                console.log('Токен не найден, перенаправление на страницу входа');
                window.location.replace('/');
                return; // Теперь return работает, так как мы внутри функции
            }
            
            console.log('Скрипт инициализирован, API_URL:', API_URL);
        
        // Настраиваем marked.js для поддержки GFM таблиц (вызываем один раз при загрузке)
        if (typeof marked !== 'undefined') {
            marked.setOptions({
                gfm: true,
                breaks: true,
                tables: true
            });
        }
        
        // Функция для добавления токена в заголовки запросов
        function getAuthHeaders() {
            const token = localStorage.getItem('auth_token');
            return {
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'application/json'
            };
        }
        
        
        let currentRequestId = null;
        let currentMarkdown = null;
        let originalMarkdown = null; // Сохраняем оригинальный markdown отдельно
        let currentTranslatedMarkdown = null; // Сохраняем переведенный markdown
        let currentResult = null;
        window.currentResult = null;
        window.currentFilter = 'all'; // Глобальная копия для фильтра
        let currentSeed = null;
        let generationStartTime = null;
        let originalRubric = null;
        let originalTextStats = null;
        let regeneratedRubric = null;
        let regeneratedTextStats = null;
        let currentMetricsVersion = 'original'; // 'original' или 'regenerated'
        let currentReportVersion = 'original'; // 'original' или 'regenerated'

        const appStores = window.ContentGenStores || {};
        const generationStore = appStores.generationStore || null;
        const resultStore = appStores.resultStore || null;
        const workflowProfileStore = appStores.workflowProfileStore || null;

        function setGenerationStoreState(updates = {}) {
            if (generationStore && typeof generationStore.setState === 'function') {
                generationStore.setState(updates);
            }
        }

        function setResultStoreState(updates = {}) {
            if (resultStore && typeof resultStore.setState === 'function') {
                resultStore.setState(updates);
            }
        }

        function normalizeWorkflowProfile(profile) {
            if (typeof appStores.normalizeWorkflowProfile === 'function') {
                return appStores.normalizeWorkflowProfile(profile);
            }
            const standard = {
                id: 'standard',
                title: 'Обычный режим',
                capabilities: {
                    project_regeneration: true,
                    section_regeneration: true,
                    methodology_assistant: false,
                    stage_review: false,
                    final_readme_editing: true,
                    checklist_editing: true
                }
            };
            const methodology = {
                id: 'methodology',
                title: 'Методологический режим',
                capabilities: {
                    project_regeneration: true,
                    section_regeneration: true,
                    methodology_assistant: true,
                    stage_review: true,
                    final_readme_editing: true,
                    checklist_editing: true
                }
            };
            if (profile && typeof profile === 'object') {
                const base = profile.id === 'methodology' ? methodology : standard;
                const capabilities = { ...base.capabilities, ...(profile.capabilities || {}) };
                if (base.id === 'methodology') {
                    capabilities.project_regeneration = true;
                    capabilities.section_regeneration = true;
                }
                return {
                    ...base,
                    ...profile,
                    capabilities
                };
            }
            return profile === 'methodology' ? methodology : standard;
        }

        function workflowProfileFromSeed(seed = currentSeed) {
            return normalizeWorkflowProfile(seed?.methodology_human_review ? 'methodology' : 'standard');
        }

        function setWorkflowProfileState(profile = null) {
            const resolved = normalizeWorkflowProfile(profile || workflowProfileFromSeed());
            workflowProfileStore?.setState?.({
                profile: resolved,
                profileId: resolved.id,
                capabilities: resolved.capabilities || {}
            });
            return resolved;
        }

        function getWorkflowProfileState() {
            const stored = workflowProfileStore?.getState?.()?.profile;
            return normalizeWorkflowProfile(stored || workflowProfileFromSeed());
        }

        function getWorkflowCapability(capabilityName) {
            const profile = getWorkflowProfileState();
            const capabilities = profile?.capabilities || {};
            if (Object.prototype.hasOwnProperty.call(capabilities, capabilityName)) {
                return Boolean(capabilities[capabilityName]);
            }
            return false;
        }

        function isProjectRegenerationEnabled() {
            return getWorkflowCapability('project_regeneration');
        }

        function syncStoresFromLocalState() {
            setGenerationStoreState({
                currentRequestId,
                currentMarkdown,
                originalMarkdown,
                currentTranslatedMarkdown,
                currentResult,
                currentSeed,
                generationStartTime,
                lastKnownGenerationPhase,
                lastKnownGenerationProgress,
                lastKnownGenerationAgent,
                currentGenerationStatus,
                lastGenerationError,
            });
            setResultStoreState({
                originalRubric,
                originalTextStats,
                regeneratedRubric,
                regeneratedTextStats,
                regeneratedMarkdown: window.regeneratedMarkdown || null,
                currentRubric: window.currentRubric || null,
                currentMetricsVersion,
                currentReportVersion,
            });
        }

        function setCurrentResult(result) {
            currentResult = result;
            window.currentResult = result;
            window.__contentGenCurrentResult = result;
            setGenerationStoreState({ currentResult: result });
        }
        let lastKnownGenerationPhase = null;
        let lastKnownGenerationProgress = 0;
        let lastKnownGenerationAgent = 'Инициализация...';
        let currentGenerationStatus = 'idle';
        let lastGenerationError = null;

        window.ContentGenGenerationRuntime = {
            getApiUrl: () => API_URL,
            getAuthHeaders,
            getState: () => {
                syncStoresFromLocalState();
                if (appStores && typeof appStores.getState === 'function') {
                    return appStores.getState();
                }
                return {
                    currentRequestId,
                    currentMarkdown,
                    originalMarkdown,
                    currentTranslatedMarkdown,
                    currentResult,
                    currentSeed,
                    generationStartTime,
                    lastKnownGenerationPhase,
                    lastKnownGenerationProgress,
                    lastKnownGenerationAgent,
                    currentGenerationStatus,
                    lastGenerationError,
                    currentMetricsVersion,
                    originalRubric,
                    originalTextStats,
                    regeneratedRubric,
                    regeneratedTextStats,
                    regeneratedMarkdown: window.regeneratedMarkdown || null,
                    workflowProfile: getWorkflowProfileState(),
                    workflowCapabilities: getWorkflowProfileState().capabilities || {},
                };
            },
            setState: (updates = {}) => {
                if (Object.prototype.hasOwnProperty.call(updates, 'currentRequestId')) currentRequestId = updates.currentRequestId;
                if (Object.prototype.hasOwnProperty.call(updates, 'currentMarkdown')) currentMarkdown = updates.currentMarkdown;
                if (Object.prototype.hasOwnProperty.call(updates, 'originalMarkdown')) originalMarkdown = updates.originalMarkdown;
                if (Object.prototype.hasOwnProperty.call(updates, 'currentTranslatedMarkdown')) currentTranslatedMarkdown = updates.currentTranslatedMarkdown;
                if (Object.prototype.hasOwnProperty.call(updates, 'currentResult')) setCurrentResult(updates.currentResult);
                if (Object.prototype.hasOwnProperty.call(updates, 'currentSeed')) {
                    currentSeed = updates.currentSeed;
                    if (
                        !Object.prototype.hasOwnProperty.call(updates, 'workflowProfile')
                        && !Object.prototype.hasOwnProperty.call(updates, 'workflow_profile')
                    ) {
                        setWorkflowProfileState(workflowProfileFromSeed(currentSeed));
                    }
                }
                if (Object.prototype.hasOwnProperty.call(updates, 'workflowProfile')) {
                    setWorkflowProfileState(updates.workflowProfile);
                }
                if (Object.prototype.hasOwnProperty.call(updates, 'workflow_profile')) {
                    setWorkflowProfileState(updates.workflow_profile);
                }
                if (Object.prototype.hasOwnProperty.call(updates, 'generationStartTime')) generationStartTime = updates.generationStartTime;
                if (Object.prototype.hasOwnProperty.call(updates, 'lastKnownGenerationPhase')) lastKnownGenerationPhase = updates.lastKnownGenerationPhase;
                if (Object.prototype.hasOwnProperty.call(updates, 'lastKnownGenerationProgress')) lastKnownGenerationProgress = Number(updates.lastKnownGenerationProgress || 0);
                if (Object.prototype.hasOwnProperty.call(updates, 'lastKnownGenerationAgent')) lastKnownGenerationAgent = updates.lastKnownGenerationAgent;
                if (Object.prototype.hasOwnProperty.call(updates, 'currentGenerationStatus')) currentGenerationStatus = updates.currentGenerationStatus;
                if (Object.prototype.hasOwnProperty.call(updates, 'lastGenerationError')) lastGenerationError = updates.lastGenerationError;
                if (Object.prototype.hasOwnProperty.call(updates, 'originalRubric') && updates.originalRubric !== undefined) {
                    originalRubric = updates.originalRubric;
                    window.currentRubric = updates.originalRubric;
                }
                if (Object.prototype.hasOwnProperty.call(updates, 'originalTextStats') && updates.originalTextStats !== undefined) {
                    originalTextStats = updates.originalTextStats;
                }
                if (Object.prototype.hasOwnProperty.call(updates, 'regeneratedRubric') && updates.regeneratedRubric !== undefined) {
                    regeneratedRubric = updates.regeneratedRubric;
                    window.regeneratedRubric = updates.regeneratedRubric;
                }
                if (Object.prototype.hasOwnProperty.call(updates, 'regeneratedTextStats') && updates.regeneratedTextStats !== undefined) {
                    regeneratedTextStats = updates.regeneratedTextStats;
                    window.regeneratedTextStats = updates.regeneratedTextStats;
                }
                if (Object.prototype.hasOwnProperty.call(updates, 'regeneratedMarkdown') && updates.regeneratedMarkdown !== undefined) {
                    window.regeneratedMarkdown = updates.regeneratedMarkdown;
                }
                syncStoresFromLocalState();
                updateRegenerationAvailability();
            },
            setCurrentResult,
            showMethodologyAssistantChat: (status) => window.MethodologyAssistantChat?.show(status),
            hideMethodologyAssistantChat: () => window.MethodologyAssistantChat?.hide(),
            updateAssistantChatStatus: (status, stageId) => window.MethodologyAssistantChat?.updateStatus(status, stageId),
            appendAssistantChatMessage: (role, text) => window.MethodologyAssistantChat?.appendMessage(role, text),
        };

        window.MethodologyAssistantChat?.configure({
            getApiUrl: () => API_URL,
            getAuthHeaders,
            getState: () => window.ContentGenGenerationRuntime.getState(),
            isEnabled: () => getWorkflowCapability('methodology_assistant'),
            onApproved: (requestId) => resumeGenerationAfterMethodologyApproval(requestId),
            showMethodologyReviewActions: (requestId, message) => showMethodologyReviewActions(requestId, message),
        });

        window.methodologyPanel?.configure({
            apiUrl: API_URL,
            getAuthHeaders,
            getCurrentRequestId: () => currentRequestId,
            onApproved: (requestId) => resumeGenerationAfterMethodologyApproval(requestId),
            onDiffApproved: (_requestId, reviewState) => {
                applyAcceptedMethodologyPreview(reviewState);
            },
            onRejected: (_requestId, comment) => {
                stopGenerationTracking();
                hideCancelButton();
                document.body.classList.remove('generation-running', 'generation-stage-review');
                const logContent = document.getElementById('logContent');
                if (logContent) {
                    const text = comment
                        ? `Генерация остановлена методологом: ${comment}`
                        : 'Генерация остановлена методологом.';
                    if (window.sanitize) {
                        window.sanitize.safeSetErrorMessage(logContent, text);
                    } else {
                        logContent.textContent = text;
                    }
                }
                const btn = document.getElementById('generateBtn');
                if (btn) {
                    btn.disabled = false;
                    btn.textContent = 'Сгенерировать';
                }
                const noResults = document.getElementById('noResults');
                const resultsArea = document.getElementById('resultsArea');
                if (!currentResult && noResults && resultsArea?.style.display === 'none') {
                    noResults.style.display = 'block';
                }
            },
            onChangeRequested: (_requestId, payload) => {
                const logContent = document.getElementById('logContent');
                if (logContent) {
                    const text = `Методолог запросил правки: ${payload.instruction}`;
                    if (window.sanitize) {
                        window.sanitize.safeSetHTML(logContent, `<div class="info-box">${window.sanitize.escapeHtml(text)}</div>`);
                    } else {
                        logContent.textContent = text;
                    }
                }
            },
            onError: (message) => {
                if (window.toast) {
                    window.toast.error(message);
                }
            }
        });

        function resumeGenerationAfterMethodologyApproval(requestId) {
            currentGenerationStatus = 'in_progress';
            showCompactGenerationProgress('Генерация проекта продолжается...', {
                progress: Math.max(lastKnownGenerationProgress || 0, 1),
                agent: lastKnownGenerationAgent || 'Продолжение генерации'
            });
            showGenerationRunView(currentSeed || {}, {
                phase: lastKnownGenerationPhase || 'initialization',
                status: 'in_progress',
                progress: Math.max(lastKnownGenerationProgress || 0, 1),
                agent: lastKnownGenerationAgent || 'Продолжение генерации',
                message: 'Применяем решение методолога и продолжаем пайплайн.'
            });
            const cancelBtn = document.getElementById('cancelGenerationBtn');
            if (cancelBtn) {
                cancelBtn.style.setProperty('display', 'block', 'important');
                cancelBtn.disabled = false;
            }
            const btn = document.getElementById('generateBtn');
            if (btn) {
                btn.disabled = true;
                btn.textContent = 'Генерация...';
            }
            startTimer();
            pollGenerationStatus(requestId);
        }

        function isMethodologyReviewEnabled() {
            return getWorkflowCapability('stage_review');
        }

        function updateRegenerationAvailability() {
            const disabled = !isProjectRegenerationEnabled();
            document.body.classList.toggle('methodology-regeneration-disabled', disabled);
            document.querySelectorAll('.regen-only-action, #regen').forEach((node) => {
                node.hidden = disabled;
            });
            if (disabled && document.getElementById('regen')?.classList.contains('active')) {
                if (typeof window.activateResultTab === 'function') {
                    window.activateResultTab('readme');
                } else {
                    showTab('readme');
                }
            }
        }

        function applyAcceptedMethodologyPreview(reviewState) {
            const markdown = reviewState?.preview_markdown
                || reviewState?.result?.markdown
                || reviewState?.markdown
                || '';
            if (!markdown || typeof markdown !== 'string') {
                return;
            }

            currentMarkdown = markdown;
            if (!originalMarkdown) {
                originalMarkdown = markdown;
            }
            setCurrentResult({
                ...(currentResult || {}),
                markdown
            });

            const readmeContainer = document.getElementById('readmeContent');
            if (readmeContainer) {
                if (document.body.classList.contains('generation-completed')) {
                    renderResultReadme(markdown);
                } else {
                    displayMarkdown(markdown, 'readmeContent');
                }
            }

            const previewContainer = document.getElementById('readmePreview');
            if (previewContainer) {
                displayMarkdown(markdown, 'readmePreview');
            }

            saveGenerationState();
        }
        
        // Инициализация Mermaid в светлой теме, чтобы диаграммы соответствовали продуктовой палитре.
        if (typeof mermaid !== 'undefined') {
            mermaid.initialize({ 
                startOnLoad: false, 
                securityLevel: 'loose',
                suppressErrorRendering: true,
                theme: 'base',
                flowchart: {
                    htmlLabels: true,
                    curve: 'basis',
                    padding: 12,
                    nodeSpacing: 36,
                    rankSpacing: 42,
                    wrappingWidth: 180,
                    useMaxWidth: true
                },
                themeVariables: {
                    primaryColor: '#ffffff',
                    primaryTextColor: '#111820',
                    primaryBorderColor: '#9aa79d',
                    lineColor: '#334238',
                    secondaryColor: '#eef4ef',
                    tertiaryColor: '#f7faf6',
                    background: '#ffffff',
                    mainBkg: '#ffffff',
                    secondBkg: '#eef4ef',
                    textColor: '#111820',
                    border1: '#9aa79d',
                    border2: '#7f8d83',
                    arrowheadColor: '#334238',
                    edgeLabelBackground: '#ffffff',
                    fontSize: '14px',
                    fontFamily: '-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Arial, sans-serif'
                }
            });
        } else {
            console.warn('[Mermaid] Скрипт Mermaid не загружен на этой странице.');
        }
        
        function toggleGroupSize() {
            const projectType = document.getElementById('projectType')?.value || 'individual';
            const groupSizeGroup = document.getElementById('groupSizeGroup');
            if (groupSizeGroup) {
                groupSizeGroup.style.display = projectType === 'group' ? 'block' : 'none';
            }
        }
        
        function toggleBonusWish() {
            const generateBonus = getChecked('generateBonus');
            setDisplay('bonusWishGroup', generateBonus ? 'block' : 'none');
        }
        
        function toggleExpander(id) {
            // Пытаемся найти элемент по ID
            const contentElement = document.getElementById(id);
            if (!contentElement) {
                console.error(`Элемент с ID "${id}" не найден`);
                return;
            }
            
            // Находим родительский элемент .expander
            let expander = contentElement.closest('.expander');
            
            // Если не нашли через closest, пытаемся найти напрямую
            if (!expander) {
                expander = contentElement.parentElement;
                while (expander && !expander.classList.contains('expander')) {
                    expander = expander.parentElement;
                }
            }
            
            if (expander) {
                const isOpen = expander.classList.contains('open');
                expander.classList.toggle('open');
                console.log(`Expander "${id}" ${isOpen ? 'закрыт' : 'открыт'}`);
            } else {
                console.error(`Родительский элемент .expander не найден для элемента "${id}"`);
                // Пытаемся найти напрямую по классу
                const allExpanders = document.querySelectorAll('.expander');
                console.log(`Найдено expanders на странице: ${allExpanders.length}`);
            }
        }

        async function clearForm() {
            // Очищаем состояние генерации при очистке формы
            clearGenerationState();
            resetGeneratorChrome();
            
            // Используем модальное окно для подтверждения
            let confirmed = false;
            if (window.modal) {
                confirmed = await window.modal.confirm(
                    'Очистить форму',
                    'Вы уверены, что хотите очистить все поля формы? Это действие нельзя отменить.',
                    'Очистить',
                    'Отмена'
                );
            } else {
                confirmed = confirm('Очистить все поля формы?');
            }
            
            if (!confirmed) return;
            
            {
                // Очищаем все поля формы вручную
                
                // Базовые параметры
                setValue('projectType', 'individual');
                setValue('groupSize', '3');
                setDisplay('groupSizeGroup', 'none');
                
                // Тематический блок
                setValue('thematicBlock', 'BSA');
                setValue('newBlockName', '');
                setValue('newBlockCode', '');
                setDisplay('addBlockExpander', 'none');
                
                // Остальные поля
                setAudienceLevel('beginner_plus');
                setValue('titleSeed', '');
                setValue('requiredTools', '');
                setValue('requiredSoftware', '');
                setValue('storytellingType', 'sjm');
                setValue('storytelling', '');
                setValue('projectDescription', '');
                setValue('learningOutcomes', '');
                setValue('skills', '');
                setValue('projectContentType', '');
                setValue('platformName', '');
                setValue('workloadHours', '');
                setValue('additionalMaterials', '');
                
                // Настройки репозитория
                setValue('repoBaseUrl', '');
                setValue('repoPathTemplate', 'repo/part-03/task-{num:02d}/README.md');
                
                // Бонусное задание
                setChecked('generateBonus', false);
                setValue('bonusWish', '');
                setDisplay('bonusWishGroup', 'none');
                
                setChecked('methodologyHumanReview', false);
                setWorkflowProfileState('standard');
                updateRegenerationAvailability();
                setChecked('includeFormulas', false);
                setChecked('includeTables', false);
                setChecked('includeDiagrams', false);
                
                console.log('✅ Форма очищена');
                if (window.toast) {
                    window.toast.success('Форма успешно очищена');
                }
            }
        }
        
        async function clearGeneration() {
            // Используем модальное окно для подтверждения
            let confirmed = false;
            if (window.modal) {
                confirmed = await window.modal.confirm(
                    'Очистить генерацию',
                    'Вы уверены, что хотите очистить результаты генерации? Это действие нельзя отменить.',
                    'Очистить',
                    'Отмена'
                );
            } else {
                confirmed = confirm('Очистить результаты генерации?');
            }
            
            if (!confirmed) return;
            
            currentRequestId = null;
            currentMarkdown = null;
            originalMarkdown = null;
            setCurrentResult(null);
            currentSeed = null;
            setWorkflowProfileState('standard');
            syncStoresFromLocalState();
            updateRegenerationAvailability();
            clearGenerationState(); // Очищаем сохраненное состояние
            resetGeneratorChrome();
            setDisplay('noResults', 'block');
            setDisplay('resultsArea', 'none');
            
            if (window.toast) {
                window.toast.success('Результаты генерации очищены');
            }
        }
        
        function clearRegeneration() {
            setValue('regenerationComments', '');
            const regenContent = document.getElementById('regenContent');
            if (regenContent) regenContent.innerHTML = '';
            setDisplay('regenerationChanges', 'none');
            window.clearRegenerationSectionComments?.();
            // Сбрасываем версии на оригинальные
            currentMetricsVersion = 'original';
            currentReportVersion = 'original';
            setResultStoreState({ currentMetricsVersion, currentReportVersion, regeneratedMarkdown: null });
            if (originalRubric) {
                window.currentRubric = originalRubric;
                displayMetrics(originalRubric, 'metricsContentOriginal');
                switchMetricsVersion('original');
            }
            if (originalTextStats) {
                displayReport({ text_stats: originalTextStats }, 'reportContentOriginal');
                switchReportVersion('original');
            }
            // Обновляем переключатели
            updateVersionButtons();
        }
        
        function switchMetricsVersion(version, clickedElement = null) {
            currentMetricsVersion = version;
            window.currentMetricsVersion = version; // Обновляем глобальную переменную
            setResultStoreState({ currentMetricsVersion: version });
            
            // Переключаем вкладки
            const tabOriginal = document.getElementById('metricsTabOriginal');
            const tabRegen = document.getElementById('metricsTabRegen');
            if (tabOriginal && tabRegen) {
                tabOriginal.classList.remove('active');
                tabRegen.classList.remove('active');
                if (version === 'original') {
                    tabOriginal.classList.add('active');
                } else {
                    tabRegen.classList.add('active');
                }
            }
            
            // Переключаем контейнеры
            const containerOriginal = document.getElementById('metricsContentOriginal');
            const containerRegen = document.getElementById('metricsContentRegen');
            if (containerOriginal && containerRegen) {
                if (version === 'original') {
                    containerOriginal.style.display = 'block';
                    containerRegen.style.display = 'none';
                    if (originalRubric) {
                        window.currentRubric = originalRubric;
                        setResultStoreState({ currentRubric: originalRubric });
                        displayMetrics(originalRubric, 'metricsContentOriginal');
                        console.log('✅ Переключено на оригинальные метрики, отображено:', originalRubric.items?.length || 0, 'критериев');
                    } else {
                        console.warn('⚠️ originalRubric отсутствует, контейнер пуст');
                        containerOriginal.innerHTML = '<div class="info-box">Оригинальные метрики недоступны</div>';
                    }
                } else {
                    containerOriginal.style.display = 'none';
                    containerRegen.style.display = 'block';
                    if (regeneratedRubric) {
                        window.currentRubric = regeneratedRubric;
                        setResultStoreState({ currentRubric: regeneratedRubric });
                        displayMetrics(regeneratedRubric, 'metricsContentRegen');
                        console.log('✅ Переключено на перегенерированные метрики, отображено:', regeneratedRubric.items?.length || 0, 'критериев');
                    } else {
                        console.warn('⚠️ regeneratedRubric отсутствует, контейнер пуст');
                        containerRegen.innerHTML = '<div class="info-box">Перегенерированные метрики недоступны</div>';
                    }
                }
            } else {
                console.error('❌ Контейнеры метрик не найдены:', {
                    original: !!containerOriginal,
                    regen: !!containerRegen
                });
            }
        }
        
        function switchReportVersion(version, clickedElement = null) {
            currentReportVersion = version;
            window.currentReportVersion = version; // Обновляем глобальную переменную
            setResultStoreState({ currentReportVersion: version });
            
            // Переключаем вкладки
            const tabOriginal = document.getElementById('reportTabOriginal');
            const tabRegen = document.getElementById('reportTabRegen');
            if (tabOriginal && tabRegen) {
                tabOriginal.classList.remove('active');
                tabRegen.classList.remove('active');
                if (version === 'original') {
                    tabOriginal.classList.add('active');
                } else {
                    tabRegen.classList.add('active');
                }
            }
            
            // Переключаем контейнеры
            const containerOriginal = document.getElementById('reportContentOriginal');
            const containerRegen = document.getElementById('reportContentRegen');
            if (containerOriginal && containerRegen) {
                if (version === 'original') {
                    containerOriginal.style.display = 'block';
                    containerRegen.style.display = 'none';
                    if (originalTextStats) {
                        displayReport({ text_stats: originalTextStats }, 'reportContentOriginal');
                    }
                } else {
                    containerOriginal.style.display = 'none';
                    containerRegen.style.display = 'block';
                    if (regeneratedTextStats) {
                        displayReport({ text_stats: regeneratedTextStats }, 'reportContentRegen');
                    }
                }
            }
            if (document.body.classList.contains('generation-completed')) {
                setCompletedChrome('metrics');
            }
        }
        
        function updateVersionButtons() {
            console.log('updateVersionButtons вызвана', { 
                regeneratedRubric: !!regeneratedRubric,
                regeneratedRubricItems: regeneratedRubric?.items?.length || 0,
                regeneratedTextStats: !!regeneratedTextStats,
                originalRubric: !!originalRubric,
                originalRubricItems: originalRubric?.items?.length || 0
            });
            
            // Показываем/скрываем переключатель метрик (вкладки)
            const metricsSwitcher = document.getElementById('metricsVersionSwitcher');
            if (metricsSwitcher) {
                if (regeneratedRubric && regeneratedRubric.items && regeneratedRubric.items.length > 0) {
                    metricsSwitcher.style.display = 'block';
                    console.log('✅ Переключатель метрик показан (есть перегенерированные метрики)');
                } else {
                    metricsSwitcher.style.display = 'none';
                    console.log('❌ Переключатель метрик скрыт (нет regeneratedRubric или он пуст)', {
                        hasRegeneratedRubric: !!regeneratedRubric,
                        itemsCount: regeneratedRubric?.items?.length || 0
                    });
                }
            } else {
                console.error('❌ Элемент metricsVersionSwitcher не найден в DOM');
            }
            
            // Показываем/скрываем переключатель отчета (вкладки)
            const reportSwitcher = document.getElementById('reportVersionSwitcher');
            if (reportSwitcher) {
                if (regeneratedTextStats) {
                    reportSwitcher.style.display = 'block';
                    console.log('✅ Переключатель отчета показан');
                } else {
                    reportSwitcher.style.display = 'none';
                    console.log('❌ Переключатель отчета скрыт (нет regeneratedTextStats)');
                }
            } else {
                console.error('❌ Элемент reportVersionSwitcher не найден в DOM');
            }
        }
        
        function getCurrentRubric() {
            return currentMetricsVersion === 'regenerated' && regeneratedRubric ? regeneratedRubric : originalRubric;
        }

        function setCompletedChrome(tabName = 'readme') {
            const isMetrics = tabName === 'metrics';
            document.body.classList.add('generation-completed');
            document.body.classList.toggle('generation-metrics-view', isMetrics);
            document.body.classList.remove('generation-running', 'generation-stage-review');

            const rubricSummary = getRubricSummary(getCurrentRubric());
            const scoreText = rubricSummary.max > 0 ? `${rubricSummary.total} из ${rubricSummary.max}` : 'метрики';
            if (isMetrics) {
                setGeneratorBrand('03.4', 'МЕТРИКИ', `${scoreText} критериев`);
                setGeneratorSubbar({
                    backText: getResultDisplayTitle(),
                    backHref: '#',
                    title: 'Метрики качества',
                    statusText: rubricSummary.max > 0 ? `✓ ${scoreText}` : 'МЕТРИКИ',
                    statusClass: 'success',
                    rightHtml: `
                        <button class="btn btn-secondary btn-sm regen-only-action" type="button" onclick="fillCommentsFromFailedCriteria()">Заполнить из непройденных</button>
                        <button class="btn btn-sm regen-only-action" type="button" onclick="openRegenerationFromMetrics()">Перегенерировать</button>
                    `
                });
                updateRegenerationAvailability();
                const back = document.getElementById('generatorBackLink');
                if (back) {
                    back.onclick = (event) => {
                        event.preventDefault();
                        activateResultTab('readme');
                    };
                }
                return;
            }

            const elapsed = formatGenerationElapsed();
            setGeneratorBrand('03.3', 'РЕЗУЛЬТАТЫ', 'README + TOC');
            setGeneratorSubbar({
                backText: '← К параметрам',
                backHref: '#',
                title: getResultDisplayTitle(),
                statusText: elapsed ? `✓ ГОТОВО · ${elapsed}` : '✓ ГОТОВО',
                statusClass: 'success',
                rightHtml: `
                    <button class="btn btn-secondary btn-sm" type="button" onclick="showTab('regen', document.querySelector('.result-tabs .tab[onclick*=regen]'))">Перегенерация</button>
                    <button class="btn btn-sm" type="button" onclick="downloadResults()" id="downloadBtn">↓ Скачать архив</button>
                `
            });
            const back = document.getElementById('generatorBackLink');
            if (back) {
                back.onclick = (event) => {
                    event.preventDefault();
                    document.body.classList.remove('generation-completed', 'generation-metrics-view');
                    const noResults = document.getElementById('noResults');
                    const resultsArea = document.getElementById('resultsArea');
                    if (resultsArea) resultsArea.style.display = 'none';
                    if (noResults) noResults.style.display = 'block';
                    resetGeneratorChrome();
                };
            }
        }

        async function generateContent() {
            // Валидация формы
            if (!window.validator || !window.validator.validateGenerationForm()) {
                if (window.toast) {
                    window.toast.error('Пожалуйста, исправьте ошибки в форме перед генерацией');
                }
                return;
            }

            const btn = document.getElementById('generateBtn');
            
            // Используем loading manager для кнопки
            if (window.loading) {
                window.loading.setButtonLoading(btn, true, 'Сгенерировать');
            } else {
                btn.disabled = true;
                btn.textContent = 'Генерация...';
            }
            
            // Показываем кнопку остановки СРАЗУ при нажатии на "Сгенерировать" - ПРЯМО ЗДЕСЬ
            const cancelBtn = document.getElementById('cancelGenerationBtn');
            if (cancelBtn) {
                // Принудительно показываем кнопку
                cancelBtn.style.setProperty('display', 'block', 'important');
                cancelBtn.style.setProperty('visibility', 'visible', 'important');
                cancelBtn.style.setProperty('opacity', '1', 'important');
                cancelBtn.style.setProperty('width', '100%', 'important');
                cancelBtn.style.setProperty('margin-top', '0.5rem', 'important');
                cancelBtn.style.setProperty('height', 'auto', 'important');
                cancelBtn.disabled = false;
                
                // Проверяем через небольшую задержку
                setTimeout(() => {
                    const computed = window.getComputedStyle(cancelBtn);
                    const rect = cancelBtn.getBoundingClientRect();
                    const isVisible = computed.display !== 'none' && 
                                    computed.visibility !== 'hidden' && 
                                    rect.width > 0 && 
                                    rect.height > 0;
                    console.log('✅ Кнопка остановки (проверка через 100ms):', {
                        inlineDisplay: cancelBtn.style.display,
                        computedDisplay: computed.display,
                        computedVisibility: computed.visibility,
                        rect: { width: rect.width, height: rect.height, top: rect.top },
                        isVisible: isVisible
                    });
                    if (!isVisible) {
                        console.error('❌ КРИТИЧЕСКАЯ ОШИБКА: Кнопка не видна после установки стилей!');
                    }
                }, 100);
            } else {
                console.error('❌ КРИТИЧЕСКАЯ ОШИБКА: Кнопка cancelGenerationBtn не найдена в DOM!');
            }
            
            // Останавливаем предыдущий таймер, если есть
            stopGenerationTracking();
            lastKnownGenerationPhase = null;
            lastKnownGenerationProgress = 0;
            lastKnownGenerationAgent = 'Инициализация...';
            currentGenerationStatus = 'in_progress';
            
            // Запускаем таймер
            generationStartTime = Date.now();
            updateTimer(); // Обновляем сразу
            
            // Запускаем таймер с улучшенным механизмом обновления
            startTimer();
            
            // Показываем область логов
            const generationLogs = document.getElementById('generationLogs');
            const logContent = document.getElementById('logContent');
            if (generationLogs && logContent) {
                generationLogs.style.display = 'block';
                setGenerationStatusActive(true);
                updateGenerationTimeline('initialization', 'in_progress');
                const methodologyLiveStatus = document.getElementById('methodologyLiveStatus');
                if (methodologyLiveStatus) {
                    methodologyLiveStatus.style.display = 'none';
                    methodologyLiveStatus.innerHTML = '';
                }
                hideMethodologyReviewActions();
                logContent.innerHTML = '';
                
                // Создаем прогресс-бар
                let progressBarId = null;
                if (window.loading) {
                    progressBarId = window.loading.createProgressBar('logContent', 'Прогресс генерации');
                }
                
                // Добавляем spinner
                const spinnerId = window.loading ? window.loading.showSpinner('logContent', 'Генерация проекта...') : null;
                
                if (!window.loading) {
                    logContent.innerHTML = '<div class="generation-activity"><span class="generation-activity-dot"></span><span>Генерация проекта...</span></div>';
                }
                
                // Сохраняем ID для последующего обновления
                window.currentProgressBarId = progressBarId;
                window.currentSpinnerId = spinnerId;
            }
            
            // Сбрасываем отображение агента
            const agentElement = document.getElementById('currentAgent');
            if (agentElement) {
                agentElement.textContent = 'Инициализация...';
            }
            
            try {
                // Собираем данные формы
                // Form module owns DOM-to-seed mapping; this function keeps orchestration only.
                const seed = buildGenerationSeed({
                    curriculumContext: window.getCurrentCurriculumContext?.() || null
                });

                currentSeed = seed;
                setWorkflowProfileState(workflowProfileFromSeed(seed));
                showGenerationRunView(seed, {
                    phase: 'initialization',
                    status: 'in_progress',
                    progress: 1,
                    agent: 'Инициализация пайплайна'
                });
                
                const formData = new FormData();
                formData.append('seed', JSON.stringify(seed));
                
                const token = localStorage.getItem('auth_token');
                
                // Запускаем генерацию
                // Таймер уже работает в setInterval
                // Используем AbortController для возможности отмены (на будущее)
                const controller = new AbortController();
                
                const response = await fetch(`${API_URL}/generate`, {
                    method: 'POST',
                    headers: {
                        'Authorization': `Bearer ${token}`
                    },
                    body: formData,
                    signal: controller.signal
                });
                
                if (!response.ok) {
                    if (response.status === 401) {
                        // Токен истек или невалиден - перенаправляем на страницу входа
                        localStorage.removeItem('auth_token');
                        localStorage.removeItem('user_id');
                        localStorage.removeItem('username');
                        localStorage.removeItem('session_id');
                        window.location.href = '/';
                        return;
                    }
                    const error = await response.json().catch(() => ({ detail: 'Ошибка генерации' }));
                    throw new Error(error.detail || `Ошибка ${response.status}: ${response.statusText}`);
                }
                
                const data = await response.json();
                currentRequestId = data.request_id;
                currentSeed = seed; // Сохраняем данные формы
                setWorkflowProfileState(data.workflow_profile || workflowProfileFromSeed(seed));
                currentGenerationStatus = 'in_progress';
                
                // Сохраняем исходное время старта, чтобы таймер не прыгал после ответа API.
                if (!generationStartTime) {
                    generationStartTime = Date.now();
                }
                saveGenerationState();
                
                // Показываем кнопку остановки СРАЗУ после получения request_id
                showCancelButton();
                
                // Обновляем текущего агента сразу после начала генерации
                await updateCurrentAgent(currentRequestId);
                
                // Начинаем polling статуса генерации
                pollGenerationStatus(currentRequestId);
                
            } catch (error) {
                // Останавливаем таймер при ошибке
                stopGenerationTracking();
                currentGenerationStatus = 'failed';
                finishGenerationRun('failed', `Генерация не стартовала: ${error.message}`);

                if (logContent) {
                    if (window.sanitize) {
                        window.sanitize.safeSetErrorMessage(logContent, `Ошибка: ${error.message}`);
                    } else {
                        logContent.textContent = `Ошибка: ${error.message}`;
                    }
                }
                
                // Восстанавливаем кнопку и скрываем кнопку остановки
                const btn = document.getElementById('generateBtn');
                if (btn) {
                    if (window.loading) {
                        window.loading.setButtonLoading(btn, false, 'Сгенерировать');
                    } else {
                        btn.disabled = false;
                        btn.textContent = 'Сгенерировать';
                    }
                }
                hideCancelButton();
                
                // Удаляем spinner и progress bar
                if (window.loading) {
                    if (window.currentSpinnerId) {
                        window.loading.hideSpinner(window.currentSpinnerId);
                    }
                    if (window.currentProgressBarId) {
                        window.loading.removeProgressBar(window.currentProgressBarId);
                    }
                }
                
                // Показываем toast с ошибкой
                if (window.toast) {
                    window.toast.error(`Ошибка генерации: ${error.message}`);
                }
                // Не скрываем загрузчик при ошибке, чтобы пользователь видел сообщение об ошибке
            }
        }
        
        async function cancelGeneration() {
            if (window.modal) {
                const confirmed = await window.modal.confirm(
                    'Аварийная остановка',
                    'Вы уверены, что хотите остановить генерацию? Текущий процесс будет прерван.',
                    'Остановить',
                    'Отмена'
                );
                if (!confirmed) return;
            }
            if (!currentRequestId) {
                alert('Нет активной генерации для остановки');
                return;
            }
            
            const cancelBtn = document.getElementById('cancelGenerationBtn');
            if (cancelBtn) {
                cancelBtn.disabled = true;
                cancelBtn.textContent = 'Остановка...';
            }
            
            try {
                const token = localStorage.getItem('auth_token');
                const response = await fetch(`${API_URL}/generate/cancel/${currentRequestId}`, {
                    method: 'POST',
                    headers: {
                        'Authorization': `Bearer ${token}`
                    }
                });
                
                if (!response.ok) {
                    if (response.status === 401) {
                        localStorage.removeItem('auth_token');
                        window.location.href = '/';
                        return;
                    }
                    const error = await response.json().catch(() => ({ detail: 'Ошибка остановки' }));
                    throw new Error(error.detail || `Ошибка ${response.status}`);
                }
                
                const data = await response.json();
                if (data.success) {
                    // Останавливаем отслеживание
                    stopGenerationTracking();
                    
                    // Обновляем UI
                    const logContent = document.getElementById('logContent');
                    if (logContent) {
                        logContent.innerHTML = '<div class="warning-msg">Генерация остановлена пользователем</div>';
                    }
                    setGenerationStatusActive(false);
                    currentGenerationStatus = 'cancelled';
                    finishGenerationRun('cancelled');
                    
                    const btn = document.getElementById('generateBtn');
                    if (btn) {
                        btn.disabled = false;
                        btn.textContent = 'Сгенерировать';
                    }
                    
                    hideCancelButton();

                    alert('Генерация успешно остановлена');
                }
            } catch (error) {
                console.error('Ошибка при остановке генерации:', error);
                alert(`Ошибка при остановке генерации: ${error.message}`);
                
                if (cancelBtn) {
                    cancelBtn.disabled = false;
                    cancelBtn.textContent = 'Аварийная остановка генерации';
                }
            }
        }

        async function handleReadmeFileSelect(event) {
            const fileInput = event.target;
            const file = fileInput.files && fileInput.files[0];
            // Ищем элемент для отображения имени файла (может быть на разных страницах)
            const label = document.getElementById('readmeFileNameChecker') || 
                         document.getElementById('readmeFileName');
            if (!file) {
                if (label) label.textContent = '';
                return;
            }
            if (label) {
                const sizeKb = file.size ? `${Math.max(1, Math.round(file.size / 1024))} КБ` : 'размер не определён';
                label.textContent = `${sizeKb} · загружен`;
            }
            const title = document.getElementById('checkerReadmeUploadTitle');
            if (title) {
                title.textContent = file.name;
            }
            const checkerRight = document.getElementById('checkerSubbarRight');
            if (checkerRight) {
                checkerRight.textContent = `Файл выбран: ${file.name}`;
            }
        }

        async function checkReadme() {
            const checkBtn = document.getElementById('checkBtn');
            const noResults = document.getElementById('noResults');
            const resultsArea = document.getElementById('resultsArea');
            
            try {
                const fileInput = document.getElementById('readmeFile');
                if (!fileInput || !fileInput.files || !fileInput.files[0]) {
                    alert('Пожалуйста, выберите файл README.md для проверки.');
                    return;
                }
                const file = fileInput.files[0];
                const text = await file.text();
                const markdown = text.trim();
                if (!markdown) {
                    alert('Файл README пуст. Загрузите непустой файл.');
                    return;
                }

                // Показываем индикатор загрузки
                if (checkBtn) {
                    checkBtn.disabled = true;
                    const spinnerHtml = '<span class="s21-button-spinner"></span>';
                    checkBtn.innerHTML = spinnerHtml + ' Проверка...';
                }
                
                // Скрываем результаты
                if (resultsArea) {
                    resultsArea.style.display = 'none';
                }
                
                // Показываем индикатор загрузки в noResults
                if (noResults) {
                    // Убеждаемся, что элемент видим
                    noResults.style.display = 'block';
                    noResults.style.visibility = 'visible';
                    noResults.style.opacity = '1';
                    noResults.className = 'info-box';
                    // Устанавливаем содержимое с индикатором загрузки
                    noResults.innerHTML = `
                        <div class="s21-loading-state">
                            <div class="spinner"></div>
                            <p>Проверка критериев...</p>
                        </div>
                    `;
                    // Принудительно обновляем отображение
                    noResults.offsetHeight; // Trigger reflow
                }

                // Язык всегда русский для проверки README
                const language = 'ru';

                const loTextarea = document.getElementById('learningOutcomes');
                const loRaw = loTextarea ? loTextarea.value : '';
                const learningOutcomes = loRaw
                    .split('\n')
                    .map(s => s.trim())
                    .filter(Boolean);

                const body = {
                    markdown,
                    language,
                    llm_provider: window.getSelectedLlmProvider?.() || 'polza',
                    learning_outcomes: learningOutcomes.length ? learningOutcomes : null,
                };

                const response = await fetch(`${API_URL}/readme/check`, {
                    method: 'POST',
                    headers: getAuthHeaders(),
                    body: JSON.stringify(body),
                });

                if (!response.ok) {
                    if (response.status === 401) {
                        // Токен истек или невалиден - перенаправляем на страницу входа
                        localStorage.removeItem('auth_token');
                        localStorage.removeItem('user_id');
                        localStorage.removeItem('username');
                        localStorage.removeItem('session_id');
                        alert('Ваша сессия истекла. Пожалуйста, войдите в систему снова.');
                        window.location.href = '/';
                        return;
                    }
                    const error = await response.json().catch(() => ({ detail: 'Ошибка проверки README' }));
                    throw new Error(error.detail || `Ошибка ${response.status}: ${response.statusText}`);
                }

                const data = await response.json();

                // Скрываем индикатор загрузки и показываем результаты
                if (checkBtn) {
                    checkBtn.disabled = false;
                    checkBtn.innerHTML = 'Проверить README';
                }
                
                // Скрываем индикатор загрузки
                if (noResults) {
                    noResults.style.display = 'none';
                }
                
                // Показываем результаты
                if (resultsArea) {
                    resultsArea.style.display = 'block';
                }

                const warningsArea = document.getElementById('warningsArea');
                if (warningsArea) {
                    if (window.sanitize) {
                        warningsArea.innerHTML = '<div class="success-msg">Проверка выполнена успешно</div>';
                    } else {
                        warningsArea.textContent = 'Проверка выполнена успешно';
                    }
                }

                // Сохраняем результаты в sessionStorage
                const checkerResults = {
                    rubric: data.rubric,
                    text_stats: data.text_stats,
                    markdown: markdown,
                    timestamp: Date.now()
                };
                sessionStorage.setItem('checker_results', JSON.stringify(checkerResults));
                
                // Показываем кнопку очистки
                const clearBtn = document.getElementById('clearResultsBtn');
                if (clearBtn) {
                    clearBtn.style.display = 'inline-block';
                }

                if (data.rubric) {
                    // Сохраняем rubric для фильтров
                    window.checkerRubric = data.rubric;
                    // Используем новый контейнер для исходных критериев
                    displayMetrics(data.rubric, 'checkerMetricsOriginal');
                    // Обновляем отображение, если есть переключатель версий
                    if (typeof window.updateCheckerMetricsDisplay === 'function') {
                        window.updateCheckerMetricsDisplay();
                    }
                }
                if (data.text_stats) {
                    displayReport({ text_stats: data.text_stats }, 'checkerReport');
                }

                // Превью README
                displayMarkdown(markdown, 'readmePreview');

                // Активируем вкладку "Критерии" по умолчанию
                const metricsTab = Array.from(document.querySelectorAll('.tab')).find(btn =>
                    btn.textContent.includes('Критерии'),
                );
                showTab('metrics', metricsTab || null);
                
                // Показываем кнопку улучшения README всегда, предупреждение - только при оценке < 70%
                const improveSection = document.getElementById('improveReadmeSection');
                const improveWarning = document.getElementById('improveReadmeWarning');
                
                if (improveSection) {
                    // Кнопка всегда показывается
                    improveSection.style.display = 'block';
                    // Сохраняем markdown для улучшения
                    window.originalReadmeForImprovement = markdown;
                    
                    // Предупреждение показывается только при оценке < 70%
                    if (improveWarning && data.rubric) {
                        const totalScore = data.rubric.total || 0;
                        const maxScore = data.rubric.max_score || 100;
                        const percentage = maxScore > 0 ? (totalScore / maxScore) * 100 : 0;
                        
                        if (percentage < 70) {
                            improveWarning.style.display = 'block';
                        } else {
                            improveWarning.style.display = 'none';
                        }
                    } else if (improveWarning) {
                        improveWarning.style.display = 'none';
                    }
                }
            } catch (error) {
                console.error('Ошибка проверки README:', error);
                
                // Восстанавливаем кнопку при ошибке
                if (checkBtn) {
                    checkBtn.disabled = false;
                    checkBtn.innerHTML = 'Проверить README';
                }
                if (noResults) {
                    noResults.style.display = 'block';
                    noResults.innerHTML = '<p>Загрузите README и нажмите «Проверить», чтобы увидеть результаты.</p>';
                }
                
                alert(`Ошибка проверки README: ${error.message}`);
            }
        }
        
        function renderMethodologyPanel(payload, containerId = 'methodologyContent', options = {}) {
            window.methodologyPanel?.render(payload, containerId, options);
        }

        function showMethodologyReviewActions(requestId, message = '') {
            window.methodologyPanel?.showActions({ requestId, message });
        }

        function hideMethodologyReviewActions() {
            window.methodologyPanel?.hideActions();
        }

        function displayResults(data) {
            setGenerationStatusActive(false);
            document.body.classList.remove('generation-running', 'generation-stage-review');
            const runView = document.getElementById('generationRunView');
            if (runView) {
                runView.style.display = 'none';
            }
            // Проверяем наличие результата
            if (!data || !data.result) {
                console.error('❌ displayResults: отсутствует результат', data);
                const noResults = document.getElementById('noResults');
                if (noResults) {
                    noResults.style.display = 'block';
                    noResults.textContent = 'Ошибка: результат генерации отсутствует';
                }
                const logContent = document.getElementById('logContent');
                if (logContent) {
                    if (window.sanitize) {
                        window.sanitize.safeSetErrorMessage(logContent, 'Результат генерации отсутствует в ответе сервера');
                    } else {
                        logContent.textContent = '❌ Результат генерации отсутствует в ответе сервера';
                    }
                }
                return;
            }
            
            // Проверяем тип результата
            if (typeof data.result !== 'object') {
                console.error('❌ displayResults: результат не является объектом', typeof data.result, data.result);
                const noResults = document.getElementById('noResults');
                if (noResults) {
                    noResults.style.display = 'block';
                    noResults.textContent = 'Ошибка: неверный формат результата';
                }
                return;
            }
            
            // Проверяем наличие markdown
            if (typeof data.result.markdown !== 'string') {
                console.error('❌ displayResults: markdown отсутствует или имеет неверный тип', typeof data.result.markdown, data.result);
                const noResults = document.getElementById('noResults');
                if (noResults) {
                    noResults.style.display = 'block';
                    noResults.textContent = 'Ошибка: результат не содержит markdown';
                }
                return;
            }
            
            setCurrentResult(data.result);
            currentMarkdown = data.result.markdown;
            const restoredRegeneratedMarkdown = data.result.regenerated_markdown
                || data.result.regenerated_md
                || data.result.regenerated?.regenerated_md
                || data.result.report_json?.regenerated_markdown
                || data.result.report_json?.regenerated_md
                || null;
            if (restoredRegeneratedMarkdown) {
                window.regeneratedMarkdown = restoredRegeneratedMarkdown;
            }
            if (!originalMarkdown) {
                originalMarkdown = data.result.original_markdown
                    || data.result.report_json?.original_markdown
                    || currentMarkdown;
            }
            if (data.seed) {
                currentSeed = data.seed;
            }
            setWorkflowProfileState(data.workflow_profile || data.result.workflow_profile || workflowProfileFromSeed(currentSeed));
            updateRegenerationAvailability();

            setDisplay('noResults', 'none');
            setDisplay('resultsArea', 'block');
            updateGenerationResultSummary(data);
            
            // Предупреждения
            const warningsArea = document.getElementById('warningsArea');
            const filteredWarnings = (data.warnings || []).filter(w => !w.startsWith('ℹ️ План практики'));
            if (filteredWarnings.length > 0) {
                // Экранируем предупреждения перед вставкой
                const escapedWarnings = filteredWarnings.map(w => {
                    const escaped = window.sanitize ? window.sanitize.escapeHtml(w) : w;
                    return `<li>${escaped}</li>`;
                }).join('');
                const warningsHtml = `
                    <div class="warnings">
                        <strong>Предупреждения:</strong>
                        <ul>
                            ${escapedWarnings}
                        </ul>
                    </div>
                `;
                if (window.sanitize) {
                    window.sanitize.safeSetHTML(warningsArea, warningsHtml);
                } else {
                    warningsArea.innerHTML = warningsHtml;
                }
            } else {
                if (window.sanitize) {
                    warningsArea.innerHTML = '<div class="success-msg">Генерация завершена успешно</div>';
                } else {
                    warningsArea.textContent = 'Генерация завершена успешно';
                }
            }

            const plan = data.result && data.result.task_plan ? data.result.task_plan : null;
            if (plan) {
                const complexityCopy = {
                    easy: 'лёгкий уровень',
                    medium: 'средний уровень',
                    hard: 'повышенный уровень'
                };
                const complexityText = complexityCopy[plan.complexity] || plan.complexity || 'автоматически подобранный уровень';
                const planText = `План практики подготовлен: ${plan.tasks_count} задач, ${complexityText}. Подробности во вкладке «Практика».`;
                const escapedPlanText = window.sanitize ? window.sanitize.escapeHtml(planText) : planText;
                const planHtml = `<div class="info-box">${escapedPlanText}</div>`;
                if (window.sanitize) {
                    warningsArea.insertAdjacentHTML('beforeend', planHtml);
                } else {
                    warningsArea.innerHTML += planHtml;
                }
            }
            
            // README - с дополнительной проверкой
            const markdown = data.result?.markdown;
            if (typeof markdown !== 'string') {
                console.error('❌ displayResults: markdown отсутствует или не является строкой', typeof markdown, data.result);
                const readmeContainer = document.getElementById('readmeContent');
                if (readmeContainer) {
                    if (window.sanitize) {
                        window.sanitize.safeSetErrorMessage(readmeContainer, 'Ошибка: markdown отсутствует в результате генерации');
                    } else {
                        readmeContainer.textContent = 'Ошибка: markdown отсутствует в результате генерации';
                    }
                }
            } else {
                renderResultReadme(markdown);
            }
            
            // Переведенный README - проверяем наличие translated_markdown
            const translatedMarkdown = data.result?.translated_markdown || data.result?.report_json?.translated_markdown || null;
            currentTranslatedMarkdown = translatedMarkdown; // Сохраняем в глобальную переменную
            if (translatedMarkdown && translatedMarkdown !== markdown) {
                const translatedTab = document.getElementById('translatedTab');
                const translatedContainer = document.getElementById('translatedContent');
                if (translatedTab && translatedContainer) {
                    translatedTab.style.display = 'inline-block';
                    displayMarkdown(translatedMarkdown, 'translatedContent');
                }
            } else {
                const translatedTab = document.getElementById('translatedTab');
                if (translatedTab) {
                    translatedTab.style.display = 'none';
                }
            }
            
            // Метрики - проверяем разные возможные пути к данным
            const rubric = data.result?.rubric || data.result?.report_json?.rubric || null;
            if (rubric) {
                window.currentRubric = rubric;
                originalRubric = rubric; // Сохраняем оригинальные метрики
                displayMetrics(rubric, 'metricsContentOriginal');
                // Скрываем переключатель, так как еще нет перегенерированной версии
                const metricsSwitcher = document.getElementById('metricsVersionSwitcher');
                if (metricsSwitcher) {
                    metricsSwitcher.style.display = 'none';
                }
            } else {
                // Если метрики отсутствуют, показываем сообщение
                const metricsContainer = document.getElementById('metricsContentOriginal');
                if (metricsContainer) {
                    metricsContainer.innerHTML = '<div class="info-box">Метрики будут доступны после завершения генерации</div>';
                }
            }

            setCompletedChrome('readme');
            
            // Отчет - проверяем разные возможные пути к данным
            const textStats = data.result?.text_stats || data.result?.report_json?.text_stats || {};
            if (data.result && (textStats && Object.keys(textStats).length > 0)) {
                originalTextStats = textStats; // Сохраняем оригинальную статистику
                displayReport({ text_stats: textStats }, 'reportContentOriginal');
                // Скрываем переключатель, так как еще нет перегенерированной версии
                const reportSwitcher = document.getElementById('reportVersionSwitcher');
                if (reportSwitcher) {
                    reportSwitcher.style.display = 'none';
                }
            } else {
                // Если статистика отсутствует, показываем сообщение
                const reportContainer = document.getElementById('reportContentOriginal');
                if (reportContainer) {
                    reportContainer.innerHTML = '<div class="info-box">Отчет будет доступен после завершения генерации</div>';
                }
            }
            
            // Context analysis - проверяем разные возможные пути к данным
            const contextAnalysis = data.result?.context_analysis || data.result?.report_json?.context_analysis || null;
            const contextContainer = document.getElementById('contextContent');
            if (contextAnalysis && contextContainer) {
                displayContextAnalysis(contextAnalysis);
            } else if (contextContainer) {
                contextContainer.innerHTML = '<div class="info-box">Анализ контекста будет доступен после завершения генерации</div>';
            }
            
            // Обновляем кнопку скачивания
            const downloadButton = document.getElementById('downloadBtn');
            if (downloadButton) {
                downloadButton.onclick = () => downloadResults();
            }
            
            // Вкладка Практика: подробный вывод плана и CriticAgent
            renderPracticeTab(data.result);
            renderGeneratedDataTab(data.result);

            // Вкладка Методолог: gate decisions и per-phase замечания
            const methodologyPayload = data.methodology || data.result?.methodology_gate || {
                summary: data.result?.methodology_gate_summary,
                decisions: data.result?.methodology_gate_decisions
            };
            if (data.result?.methodology_revision_results) {
                methodologyPayload.methodology_revision_results = data.result.methodology_revision_results;
            }
            renderMethodologyPanel(methodologyPayload, 'methodologyContent');
            activateResultTab('readme');
        }

        function renderPracticeTab(result) {
            const planContainer = document.getElementById('practicePlanDetails');
            const criticContainer = document.getElementById('practiceCriticIssues');
            if (!planContainer && !criticContainer) return;

            const plan = result && result.task_plan ? result.task_plan : null;
            if (planContainer) {
                if (!plan) {
                    planContainer.innerHTML = '<div>План практики отсутствует.</div>';
                } else {
                    const ctx = plan.curriculum_context || {};
                    const prevTitles = (ctx.previous_nodes || []).map(n => n.title).slice(0, 2);
                    const nextTitles = (ctx.next_nodes || []).map(n => n.title).slice(0, 2);
                    const skillsToPrepare = (ctx.skills_to_prepare || []).slice(0, 4);
                    const progress = typeof ctx.progress_ratio === 'number'
                        ? Math.round(ctx.progress_ratio * 100)
                        : null;
                    const complexityCopy = {
                        easy: 'мягкий вход',
                        medium: 'сбалансированная нагрузка',
                        hard: 'интенсивный уровень'
                    };
                    const complexityText = complexityCopy[plan.complexity] || 'автоподбор сложности';
                    const tasksWord = plan.tasks_count === 1 ? 'задача' : (plan.tasks_count >= 5 ? 'задач' : 'задачи');

                    const lines = [
                        `Запланировано ${plan.tasks_count} ${tasksWord}: ${complexityText}.`,
                    ];
                    if (plan.explanation) {
                        lines.push(plan.explanation);
                    } else if (plan.rationale) {
                        lines.push(plan.rationale);
                    } else {
                        lines.push('Уровень рассчитан по истории трека и уровню аудитории.');
                    }

                    if (ctx.graph_available) {
                        if (prevTitles.length) {
                            lines.push(`Учли предыдущие проекты: ${prevTitles.join(', ')}.`);
                        }
                        if (skillsToPrepare.length) {
                            lines.push(`Проект подготавливает навыки: ${skillsToPrepare.join(', ')}.`);
                        }
                        if (nextTitles.length) {
                            lines.push(`Следующий шаг после проекта: ${nextTitles.join(', ')}.`);
                        }
                        if (progress !== null) {
                            lines.push(`Прогресс по треку ≈ ${progress}%.`);
                        }
                    }

                    let html = lines.map(text => {
                        const escaped = window.sanitize ? window.sanitize.escapeHtml(text) : text;
                        return `<p>${escaped}</p>`;
                    }).join('');
                    const stubFiles = result && result.assets && Array.isArray(result.assets.files) && result.assets.files.length;
                    if (stubFiles) {
                        const stubText = `Для ссылок из заданий автоматически добавлены ${stubFiles} заготовок файлов в архив.`;
                        const escapedStub = window.sanitize ? window.sanitize.escapeHtml(stubText) : stubText;
                        html += `<p>${escapedStub}</p>`;
                    }
                    if (window.sanitize) {
                        window.sanitize.safeSetHTML(planContainer, html);
                    } else {
                        planContainer.innerHTML = html;
                    }
                }
            }


            if (criticContainer) {
                const issues = result && Array.isArray(result.practice_critic_issues)
                    ? result.practice_critic_issues
                    : [];
                if (!issues.length) {
                    if (window.sanitize) {
                        criticContainer.textContent = 'Критичных замечаний CriticAgent не найдено.';
                    } else {
                        criticContainer.innerHTML = '<div>Критичных замечаний CriticAgent не найдено.</div>';
                    }
                } else {
                    const items = issues.map(issue => {
                        const sev = (issue.severity || 'warning').toLowerCase();
                        const cls = `severity-${sev}`;
                        const taskIndex = typeof issue.task_index === 'number' && issue.task_index > 0
                            ? `Задача ${issue.task_index}`
                            : 'Global';
                        // Экранируем все пользовательские данные
                        const escapedTaskIndex = window.sanitize ? window.sanitize.escapeHtml(taskIndex) : taskIndex;
                        const escapedKind = window.sanitize ? window.sanitize.escapeHtml(issue.kind || '') : (issue.kind || '');
                        const escapedSeverity = window.sanitize ? window.sanitize.escapeHtml(issue.severity || '') : (issue.severity || '');
                        const escapedMessage = window.sanitize ? window.sanitize.escapeHtml(issue.message || '') : (issue.message || '');
                        const escapedSuggestion = issue.suggestion ? (window.sanitize ? window.sanitize.escapeHtml(issue.suggestion) : issue.suggestion) : '';
                        return `
                            <li class="${cls}">
                                <div class="issue-header">
                                    <span>${escapedTaskIndex}</span>
                                    <span>${escapedKind} · ${escapedSeverity}</span>
                                </div>
                                <div class="issue-message">${escapedMessage}</div>
                                ${escapedSuggestion ? `<div class="issue-suggestion">💡 ${escapedSuggestion}</div>` : ''}
                            </li>
                        `;
                    }).join('');
                    if (window.sanitize) {
                        window.sanitize.safeSetHTML(criticContainer, `<ul>${items}</ul>`);
                    } else {
                        criticContainer.innerHTML = `<ul>${items}</ul>`;
                    }
                }
            }
        }

        function ensureRegenerationTemplate() {
            const field = document.getElementById('regenerationComments');
            if (!field) {
                return null;
            }
            const current = field.value.trim();
            if (!current.startsWith(REGENERATION_TEMPLATE)) {
                field.value = REGENERATION_TEMPLATE + (current ? `\n\n${current}` : '\n\n');
            }
            return field;
        }

        function insertRegenerationTemplate() {
            const field = ensureRegenerationTemplate();
            if (field) {
                field.focus();
                const len = field.value.length;
                field.setSelectionRange(len, len);
            }
        }

        function fillCommentsFromFailedCriteria() {
            try {
                // Получаем текущую активную версию метрик
                const metricsVersion = window.currentMetricsVersion || currentMetricsVersion || 'original';
                let rubric;
                if (metricsVersion === 'original' && originalRubric) {
                    rubric = originalRubric;
                } else if (metricsVersion === 'regenerated' && regeneratedRubric) {
                    rubric = regeneratedRubric;
                } else {
                    rubric = window.currentRubric || {};
                }
                
                if (!rubric || !rubric.items || rubric.items.length === 0) {
                    alert('Нет данных критериев для заполнения. Сначала сгенерируйте контент.');
                    return;
                }
                
                // Собираем все комментарии из непройденных критериев
                const failedItems = rubric.items.filter(item => {
                    const score = item.score;
                    return score !== 1 && score !== '1' && score !== true;
                });
                const comments = [];
                
                failedItems.forEach((item) => {
                    if (item.comments && item.comments.length > 0) {
                        item.comments.forEach(comment => {
                            const titlePrefix = item.title ? `${item.title}: ` : '';
                            comments.push(`${titlePrefix}${comment}`);
                        });
                    }
                });
                
                if (comments.length === 0) {
                    alert('✅ Все критерии пройдены! Нет замечаний для заполнения.');
                    return;
                }
                
                // Заполняем поле комментариев (только комментарии, без нумерации)
                const commentsField = ensureRegenerationTemplate();
                if (!commentsField) {
                    alert('Поле комментариев не найдено. Убедитесь, что вы находитесь на странице генерации.');
                    return;
                }
                const numbered = comments.map((comment, idx) => `${idx + 1}. ${comment}`).join('\n');
                const existingTail = commentsField.value.includes(REGENERATION_TEMPLATE)
                    ? commentsField.value.split(REGENERATION_TEMPLATE).slice(1).join(REGENERATION_TEMPLATE).trim()
                    : '';
                const suffix = existingTail ? `\n\n${existingTail}` : '';
                commentsField.value = `${REGENERATION_TEMPLATE}\n\n${numbered}${suffix}`;
                
                // Показываем сообщение
                alert(`✅ Заполнено ${comments.length} замечаний из непройденных критериев`);
            } catch (error) {
                console.error('Ошибка при заполнении комментариев:', error);
                alert('Ошибка при заполнении комментариев: ' + error.message);
            }
        }
        
        function clearCheckerResults() {
            if (!confirm('Вы уверены, что хотите очистить результаты проверки?')) {
                return;
            }
            
            // Очищаем sessionStorage
            sessionStorage.removeItem('checker_results');
            
            // Очищаем глобальные переменные
            window.checkerRubric = null;
            
            // Скрываем результаты
            const resultsArea = document.getElementById('resultsArea');
            const noResults = document.getElementById('noResults');
            if (resultsArea) {
                resultsArea.style.display = 'none';
            }
            if (noResults) {
                noResults.style.display = 'block';
                noResults.className = document.body.classList.contains('page-checker') ? 'checker-empty-state' : 'info-box';
                noResults.innerHTML = document.body.classList.contains('page-checker')
                    ? '<div class="generator-empty-orbit"><span></span></div><h3>Результаты появятся здесь</h3><p>Загрузите README слева и запустите проверку. После проверки можно улучшить документ.</p>'
                    : '<p>Загрузите README и нажмите «Проверить», чтобы увидеть результаты.</p>';
            }
            
            // Скрываем кнопку очистки
            const clearBtn = document.getElementById('clearResultsBtn');
            if (clearBtn) {
                clearBtn.style.display = 'none';
            }
            
            // Очищаем контейнеры
            const checkerMetrics = document.getElementById('checkerMetrics');
            const checkerMetricsOriginal = document.getElementById('checkerMetricsOriginal');
            const checkerMetricsImproved = document.getElementById('checkerMetricsImproved');
            const checkerReport = document.getElementById('checkerReport');
            const readmePreview = document.getElementById('readmePreview');
            if (checkerMetrics) checkerMetrics.innerHTML = '';
            if (checkerMetricsOriginal) checkerMetricsOriginal.innerHTML = '';
            if (checkerMetricsImproved) checkerMetricsImproved.innerHTML = '';
            if (checkerReport) checkerReport.innerHTML = '';
            if (readmePreview) readmePreview.innerHTML = '';
            const improvedTab = document.getElementById('improvedReadmeTab');
            if (improvedTab) improvedTab.style.display = 'none';
            const brandMark = document.getElementById('checkerBrandMark');
            const brandSub = document.getElementById('checkerBrandSub');
            const status = document.getElementById('checkerSubbarStatus');
            if (brandMark) brandMark.textContent = 'ПРОВЕРКА';
            if (brandSub) brandSub.textContent = '39 критериев · v 2.4';
            if (status) status.style.display = 'none';
        }
        
        function restoreCheckerResults() {
            try {
                const saved = sessionStorage.getItem('checker_results');
                if (!saved) {
                    return;
                }
                
                const results = JSON.parse(saved);
                if (!results.rubric && !results.text_stats) {
                    return;
                }
                
                // Восстанавливаем результаты
                const resultsArea = document.getElementById('resultsArea');
                const noResults = document.getElementById('noResults');
                const clearBtn = document.getElementById('clearResultsBtn');
                
                if (resultsArea && noResults) {
                    noResults.style.display = 'none';
                    resultsArea.style.display = 'block';
                }
                
                if (clearBtn) {
                    clearBtn.style.display = 'inline-block';
                }
                
                // Восстанавливаем данные
                if (results.rubric) {
                    window.checkerRubric = results.rubric;
                    // Используем новый контейнер для исходных критериев
                    displayMetrics(results.rubric, 'checkerMetricsOriginal');
                    // Обновляем отображение, если есть переключатель версий
                    if (typeof window.updateCheckerMetricsDisplay === 'function') {
                        window.updateCheckerMetricsDisplay();
                    }
                }
                if (results.text_stats) {
                    displayReport({ text_stats: results.text_stats }, 'checkerReport');
                }
                if (results.markdown) {
                    displayMarkdown(results.markdown, 'readmePreview');
                }
                
                // Показываем сообщение об успехе
                const warningsArea = document.getElementById('warningsArea');
                if (warningsArea) {
                    if (window.sanitize) {
                        warningsArea.innerHTML = '<div class="success-msg">Результаты восстановлены из предыдущей проверки</div>';
                    } else {
                        warningsArea.textContent = 'Результаты восстановлены из предыдущей проверки';
                    }
                }
            } catch (error) {
                console.error('Ошибка восстановления результатов:', error);
            }
        }
        
        function displayContextAnalysis(contextAnalysis) {
            const container = document.getElementById('contextContent');
            if (!container) {
                return;
            }
            
            if (!contextAnalysis) {
                container.innerHTML = '<div class="info-box">Анализ контекста недоступен</div>';
                return;
            }
            
            let html = '<h3>Анализ контекста</h3>';
            
            // Статистика
            const metrics = contextAnalysis.metrics || {};
            html += '<div class="metrics-grid">';
            html += `<div class="metric-card"><div class="metric-value">${contextAnalysis.similar_projects_count || 0}</div><div class="metric-label">Соседних проектов</div></div>`;
            html += `<div class="metric-card"><div class="metric-value">${metrics.skills_match_count || 0}</div><div class="metric-label">Совпадений навыков</div></div>`;
            html += `<div class="metric-card"><div class="metric-value">${metrics.lo_match_count || 0}</div><div class="metric-label">Совпадений результатов</div></div>`;
            html += `<div class="metric-card"><div class="metric-value">${metrics.projects_found || 0}</div><div class="metric-label">Найдено проектов</div></div>`;
            html += `<div class="metric-card"><div class="metric-value">${metrics.projects_filtered || 0}</div><div class="metric-label">Отфильтровано</div></div>`;
            if (metrics.min_order !== undefined && metrics.max_order !== undefined) {
                html += `<div class="metric-card"><div class="metric-value">${metrics.min_order}-${metrics.max_order}</div><div class="metric-label">Диапазон порядков</div></div>`;
            }
            html += '</div>';
            
            // Информация о поиске
            if (contextAnalysis.is_first_project) {
                html += '<div class="info-box">🆕 Это первый проект в тематическом блоке</div>';
            } else {
                html += `<div class="info-box">📚 Учтено ${contextAnalysis.similar_projects_count || 0} соседних проектов</div>`;
            }
            
            // Режим поиска
            if (contextAnalysis.search_mode) {
                html += `<div class="info-box">🔍 Режим анализа: ${contextAnalysis.search_mode === 'semantic' ? 'Семантический' : 'По учебному плану'}</div>`;
            }
            
            // Выравнивание навыков
            if (contextAnalysis.skills_alignment) {
                const skillsAlign = contextAnalysis.skills_alignment;
                const hasIntersection = skillsAlign.intersection && Array.isArray(skillsAlign.intersection) && skillsAlign.intersection.length > 0;
                const hasMissing = skillsAlign.missing && Array.isArray(skillsAlign.missing) && skillsAlign.missing.length > 0;
                
                if (hasIntersection || hasMissing) {
                    html += '<h4>📊 Выравнивание навыков</h4>';
                    html += '<div class="info-box">';
                    if (hasIntersection) {
                        html += `<strong>Совпадающие навыки (${skillsAlign.intersection.length}):</strong> ${skillsAlign.intersection.join(', ')}<br>`;
                    }
                    if (hasMissing) {
                        html += `<strong>Отсутствующие навыки (${skillsAlign.missing.length}):</strong> ${skillsAlign.missing.join(', ')}<br>`;
                    }
                    html += '</div>';
                } else {
                    // Если данных нет, показываем сообщение
                    html += '<h4>📊 Выравнивание навыков</h4>';
                    html += '<div class="info-box">Нет данных о выравнивании навыков</div>';
                }
            }
            
            // Выравнивание образовательных результатов
            if (contextAnalysis.learning_outcomes_alignment) {
                const loAlign = contextAnalysis.learning_outcomes_alignment;
                html += '<h4>📚 Выравнивание образовательных результатов</h4>';
                html += '<div class="info-box">';
                if (loAlign.continuation && loAlign.continuation.length > 0) {
                    html += `<strong>Продолжение (${loAlign.continuation.length}):</strong> ${loAlign.continuation.join('; ')}<br>`;
                }
                if (loAlign.new_outcomes && loAlign.new_outcomes.length > 0) {
                    html += `<strong>Новые результаты (${loAlign.new_outcomes.length}):</strong> ${loAlign.new_outcomes.join('; ')}<br>`;
                }
                html += '</div>';
            }
            
            // Резюме контекста
            if (contextAnalysis.context_summary) {
                html += '<h4>📝 Резюме контекста</h4>';
                const escapedSummary = window.sanitize ? window.sanitize.escapeHtml(contextAnalysis.context_summary) : contextAnalysis.context_summary;
                html += `<div class="info-box">${escapedSummary}</div>`;
            }
            
            // Нарратив
            if (contextAnalysis.narrative_anchor) {
                html += '<h4>🔗 Нарративный якорь</h4>';
                const escapedAnchor = window.sanitize ? window.sanitize.escapeHtml(contextAnalysis.narrative_anchor) : contextAnalysis.narrative_anchor;
                html += `<div class="info-box">${escapedAnchor}</div>`;
            }
            
            // Соседние проекты
            if (contextAnalysis.similar_projects && contextAnalysis.similar_projects.length > 0) {
                html += '<h4>📋 Соседние проекты</h4>';
                html += '<div class="table-container"><table><thead><tr><th>Код</th><th>Название</th><th>Порядок</th><th>Навыки</th></tr></thead><tbody>';
                contextAnalysis.similar_projects.slice(0, 10).forEach(proj => {
                    const skills = proj.skills ? (Array.isArray(proj.skills) ? proj.skills.join(', ') : proj.skills) : '-';
                    // Экранируем все пользовательские данные
                    const code = window.sanitize ? window.sanitize.escapeHtml(proj.code || proj.code_name || '-') : (proj.code || proj.code_name || '-');
                    const title = window.sanitize ? window.sanitize.escapeHtml(proj.title || '-') : (proj.title || '-');
                    const order = window.sanitize ? window.sanitize.escapeHtml(String(proj.order || '-')) : String(proj.order || '-');
                    const escapedSkills = window.sanitize ? window.sanitize.escapeHtml(skills) : skills;
                    html += `
                        <tr>
                            <td><strong>${code}</strong></td>
                            <td>${title}</td>
                            <td>${order}</td>
                            <td>${escapedSkills}</td>
                        </tr>
                    `;
                });
                html += '</tbody></table></div>';
            }
            
            // Используем санитизацию для HTML контента
            if (window.sanitize) {
                window.sanitize.safeSetHTML(container, html);
            } else {
                container.innerHTML = html;
            }
        }
        
        async function regenerateContent() {
            if (!isProjectRegenerationEnabled()) {
                alert('В методологическом режиме перегенерация проекта отключена. Используйте команды методолога в процессе ревью этапов.');
                return;
            }
            const comments = typeof window.buildRegenerationComments === 'function'
                ? window.buildRegenerationComments()
                : (document.getElementById('regenerationComments')?.value || '').trim();
            if (comments === null) {
                return;
            }
            if (!comments) {
                alert('Пожалуйста, введите комментарии по изменению README.');
                return;
            }
            const submittedInstructions = typeof window.getSelectedRegenerationInstructions === 'function'
                ? window.getSelectedRegenerationInstructions()
                : [];
            
            if (!currentMarkdown) {
                alert('Сначала сгенерируйте контент.');
                return;
            }
            
            // Проверяем наличие токена перед запросом
            const token = localStorage.getItem('auth_token');
            if (!token) {
                alert('Требуется авторизация. Перенаправление на страницу входа...');
                // Очищаем sessionStorage при редиректе на авторизацию
                sessionStorage.removeItem('generation_state');
                window.location.href = '/';
                return;
            }
            
            // Показываем загрузчик при перегенерации
            const generationLogs = document.getElementById('generationLogs');
            const logContent = document.getElementById('logContent');
            if (generationLogs && logContent) {
                generationLogs.style.display = 'block';
                
                // Создаем spinner через loading manager
                if (window.loading) {
                    const spinnerId = window.loading.showSpinner('logContent', 'Перегенерация контента...');
                    window.currentRegenSpinnerId = spinnerId;
                } else {
                    logContent.innerHTML = '<div class="loading"><div class="spinner"></div><p>Перегенерация контента...</p></div>';
                }
            }
            
            try {
                const response = await fetch(`${API_URL}/regenerate`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${token}`
                    },
                    body: JSON.stringify({
                        original_request_id: currentRequestId,
                        original_md: currentMarkdown,
                        comments: comments,
                        language: 'ru',
                        project_seed: currentSeed ? { ...currentSeed, language: 'ru' } : null
                    })
                });
                
                if (!response.ok) {
                    if (response.status === 401) {
                        // Токен истек или невалиден - перенаправляем на страницу входа
                        localStorage.removeItem('auth_token');
                        localStorage.removeItem('user_id');
                        localStorage.removeItem('username');
                        localStorage.removeItem('session_id');
                        // Очищаем sessionStorage при редиректе на авторизацию
                        sessionStorage.removeItem('generation_state');
                        window.location.href = '/';
                        return;
                    }
                    const error = await response.json().catch(() => ({ detail: 'Ошибка перегенерации' }));
                    throw new Error(error.detail || `Ошибка ${response.status}: ${response.statusText}`);
                }
                
                const data = await response.json();
                appStores.regenerationStore?.setState?.({
                    validationReport: data.validation_report || null,
                    warnings: Array.isArray(data.warnings) ? data.warnings : [],
                    accepted: data.accepted !== false,
                    rubricRegression: data.rubric_regression || null,
                });
                if (data.accepted === false) {
                    const regression = data.rubric_regression || {};
                    const warningMessage = (data.warnings && data.warnings[0])
                        || regression.message
                        || 'Перегенерация не применена: результат ухудшил rubric. Уточните запрос и попробуйте ещё раз.';
                    const failedItems = Array.isArray(regression.new_failed) && regression.new_failed.length
                        ? regression.new_failed
                        : (Array.isArray(regression.failed) ? regression.failed : []);
                    const failedHtml = failedItems.length
                        ? `<ul class="s21-plain-list">${failedItems.slice(0, 8).map(item => {
                            const id = window.sanitize ? window.sanitize.escapeHtml(item.id || '') : (item.id || '');
                            const title = window.sanitize ? window.sanitize.escapeHtml(item.title || 'Критерий') : (item.title || 'Критерий');
                            const evidence = item.evidence
                                ? ` — ${window.sanitize ? window.sanitize.escapeHtml(item.evidence) : item.evidence}`
                                : '';
                            return `<li><strong>${id}</strong> ${title}${evidence}</li>`;
                        }).join('')}</ul>`
                        : '';
                    if (window.loading && window.currentRegenSpinnerId) {
                        window.loading.hideSpinner(window.currentRegenSpinnerId);
                    }
                    if (generationLogs) {
                        generationLogs.style.display = 'block';
                    }
                    if (logContent) {
                        const html = `<div class="warning-msg"><strong>Нужно уточнить запрос перегенерации.</strong><p>${window.sanitize ? window.sanitize.escapeHtml(warningMessage) : warningMessage}</p>${failedHtml}</div>`;
                        if (window.sanitize) {
                            window.sanitize.safeSetHTML(logContent, html);
                        } else {
                            logContent.innerHTML = html;
                        }
                    }
                    if (window.toast) {
                        window.toast.warning('Перегенерация не применена: ухудшились критерии rubric.', 7000);
                    } else {
                        alert(warningMessage);
                    }
                    return;
                }
                
                // ВАЖНО: Сохраняем оригинальные данные ДО обновления currentResult
                // Иначе currentResult.rubric уже будет перегенерированным
                if (!originalRubric && currentResult && currentResult.rubric) {
                    originalRubric = currentResult.rubric;
                    console.log('✅ originalRubric сохранен при перегенерации:', originalRubric.items?.length || 0, 'критериев');
                } else if (originalRubric) {
                    console.log('✅ originalRubric уже сохранен:', originalRubric.items?.length || 0, 'критериев');
                } else {
                    console.warn('⚠️ Не удалось сохранить originalRubric: currentResult.rubric отсутствует');
                }
                
                if (!originalTextStats && currentResult && currentResult.text_stats) {
                    originalTextStats = currentResult.text_stats;
                    console.log('✅ originalTextStats сохранен при перегенерации');
                } else if (originalTextStats) {
                    console.log('✅ originalTextStats уже сохранен');
                } else {
                    console.warn('⚠️ Не удалось сохранить originalTextStats: currentResult.text_stats отсутствует');
                }
                
                // Сохраняем перегенерированный markdown отдельно
                // Сохраняем оригинальный markdown перед перегенерацией (если еще не сохранен)
                if (!originalMarkdown && currentMarkdown) {
                    originalMarkdown = currentMarkdown;
                }
                
                // Сохраняем перегенерированный markdown отдельно
                const regeneratedMarkdown = data.regenerated_md;
                window.regeneratedMarkdown = regeneratedMarkdown; // Сохраняем для скачивания
                setResultStoreState({ regeneratedMarkdown });
                
                // Обновляем текущий markdown перегенерированным
                currentMarkdown = regeneratedMarkdown;
                
                // Обновляем результат с новыми данными (ПОСЛЕ сохранения оригинальных)
                if (currentResult) {
                    currentResult.original_markdown = originalMarkdown || currentMarkdown;
                    currentResult.markdown = data.regenerated_md;
                    currentResult.regenerated_markdown = data.regenerated_md;
                    currentResult.rubric = data.rubric;
                    currentResult.text_stats = data.text_stats;
                    currentResult.regenerated = {
                        regenerated_md: data.regenerated_md,
                        changes: data.changes || [],
                        rubric: data.rubric,
                        text_stats: data.text_stats,
                    };
                    currentResult.report_json = {
                        ...(currentResult.report_json || {}),
                        markdown: data.regenerated_md,
                        regenerated_markdown: data.regenerated_md,
                        original_markdown: originalMarkdown || currentMarkdown,
                        rubric: data.rubric,
                        text_stats: data.text_stats,
                    };
                }
                
                // Сохраняем перегенерированные метрики и отчет
                if (data.rubric) {
                    regeneratedRubric = data.rubric;
                    window.regeneratedRubric = data.rubric; // Сохраняем в window для глобального доступа
                    setResultStoreState({ regeneratedRubric: data.rubric });
                    console.log('regeneratedRubric сохранен:', !!regeneratedRubric);
                }
                if (data.text_stats) {
                    regeneratedTextStats = data.text_stats;
                    window.regeneratedTextStats = data.text_stats; // Сохраняем в window для глобального доступа
                    setResultStoreState({ regeneratedTextStats: data.text_stats });
                    console.log('regeneratedTextStats сохранен:', !!regeneratedTextStats);
                }
                syncStoresFromLocalState();
                
                // Сохраняем обновленное состояние
                saveGenerationState();
                
                // Очищаем контейнер перед отображением нового контента
                const regenContainer = document.getElementById('regenContent');
                if (regenContainer) {
                    regenContainer.innerHTML = '';
                }
                
                // Отображаем перегенерированный контент с учетом активного режима просмотра
                if (typeof window.renderRegenerationReadme === 'function') {
                    window.renderRegenerationReadme(data.regenerated_md);
                } else {
                    displayMarkdown(data.regenerated_md, 'regenContent');
                }
                window.rememberRegenerationInstructions?.(submittedInstructions);
                window.renderRegenerationSectionSelector?.(data.regenerated_md);
                renderGeneratedDataTab(currentResult || { markdown: data.regenerated_md });
                
                // Отображаем список изменений
                if (data.changes && data.changes.length > 0) {
                    const changesList = document.getElementById('regenerationChangesList');
                    const changesContainer = document.getElementById('regenerationChanges');
                    if (changesList) {
                        changesList.innerHTML = '<ul class="s21-plain-list">' +
                            data.changes.map(change => `<li>${window.sanitize ? window.sanitize.escapeHtml(change) : change}</li>`).join('') +
                            '</ul>';
                    }
                    if (changesContainer) changesContainer.style.display = 'block';
                } else {
                    setDisplay('regenerationChanges', 'none');
                }
                
                // Отображаем перегенерированные метрики и отчет в их контейнерах
                if (data.rubric) {
                    console.log('Отображаем перегенерированные метрики:', {
                        itemsCount: data.rubric.items?.length || 0,
                        total: data.rubric.total,
                        max: data.rubric.max_score
                    });
                    displayMetrics(data.rubric, 'metricsContentRegen');
                    window.currentRubric = data.rubric;
                    currentMetricsVersion = 'regenerated';
                    window.currentMetricsVersion = 'regenerated';
                    switchMetricsVersion('regenerated');
                    console.log('✅ regeneratedRubric отображен в metricsContentRegen');
                } else {
                    console.warn('⚠️ data.rubric отсутствует, метрики не отображены');
                }
                if (data.text_stats) {
                    displayReport({ text_stats: data.text_stats }, 'reportContentRegen');
                    currentReportVersion = 'regenerated';
                    window.currentReportVersion = 'regenerated';
                    switchReportVersion('regenerated');
                    console.log('✅ regeneratedTextStats отображен в reportContentRegen');
                } else {
                    console.warn('⚠️ data.text_stats отсутствует, отчет не отображен');
                }
                
                // Удаляем spinner
                if (window.loading && window.currentRegenSpinnerId) {
                    window.loading.hideSpinner(window.currentRegenSpinnerId);
                }
                
                // Показываем toast об успешной перегенерации
                if (window.toast) {
                    window.toast.success('Перегенерация успешно завершена!');
                }
                
                // Показываем переключатели версий (должно быть после сохранения данных)
                console.log('Вызываем updateVersionButtons после перегенерации', {
                    hasRegeneratedRubric: !!regeneratedRubric,
                    hasRegeneratedTextStats: !!regeneratedTextStats
                });
                updateVersionButtons();
                updateRegenerationAvailability();
                
                // Убеждаемся, что переключатели видны
                const metricsSwitcher = document.getElementById('metricsVersionSwitcher');
                const reportSwitcher = document.getElementById('reportVersionSwitcher');
                if (metricsSwitcher) {
                    console.log('Состояние metricsVersionSwitcher:', {
                        display: metricsSwitcher.style.display,
                        hasRegeneratedRubric: !!regeneratedRubric
                    });
                }
                if (reportSwitcher) {
                    console.log('Состояние reportVersionSwitcher:', {
                        display: reportSwitcher.style.display,
                        hasRegeneratedTextStats: !!regeneratedTextStats
                    });
                }
                
                // Убеждаемся, что контейнеры метрик и отчетов видны в соответствующих вкладках
                // Переключаем на вкладку "Метрики" и "Отчет", чтобы пользователь видел переключатели
                const metricsTab = document.querySelector('.tab[onclick*="metrics"]');
                const reportTab = document.querySelector('.tab[onclick*="report"]');
                
                // Если есть перегенерация, показываем ее вкладку как основную по умолчанию
                if (regeneratedRubric) {
                    switchMetricsVersion('regenerated');
                }
                if (regeneratedTextStats) {
                    switchReportVersion('regenerated');
                }
                
                // Логируем состояние для отладки
                console.log('Состояние после перегенерации:', {
                    hasOriginalRubric: !!originalRubric,
                    hasRegeneratedRubric: !!regeneratedRubric,
                    hasOriginalTextStats: !!originalTextStats,
                    hasRegeneratedTextStats: !!regeneratedTextStats
                });
                
                // Скрываем загрузчик после успешной перегенерации
                if (generationLogs) {
                    generationLogs.style.display = 'none';
                }
                
                // Показываем вкладку перегенерации
                const regenTab = document.querySelector('.tab[onclick*="regen"]');
                if (regenTab && !regenTab.hidden) {
                    showTab('regen', regenTab);
                } else if (isProjectRegenerationEnabled()) {
                    showTab('regen');
                }
                
            } catch (error) {
                // Удаляем spinner
                if (window.loading && window.currentRegenSpinnerId) {
                    window.loading.hideSpinner(window.currentRegenSpinnerId);
                }
                
                // При ошибке показываем сообщение об ошибке, но не скрываем загрузчик сразу
                // чтобы пользователь увидел сообщение
                if (logContent) {
                    if (window.sanitize) {
                        window.sanitize.safeSetErrorMessage(logContent, `Ошибка перегенерации: ${error.message}`);
                    } else {
                        logContent.textContent = `Ошибка перегенерации: ${error.message}`;
                    }
                }
                
                // Показываем toast с ошибкой
                if (window.toast) {
                    window.toast.error(`Ошибка перегенерации: ${error.message}`);
                } else {
                    alert('Ошибка перегенерации: ' + error.message);
                }
                
                // Скрываем загрузчик через 3 секунды после ошибки, чтобы пользователь успел увидеть сообщение
                setTimeout(() => {
                    if (generationLogs) {
                        generationLogs.style.display = 'none';
                    }
                }, 3000);
            }
        }
        
        async function downloadResults() {
            await downloadArchive(false);
        }
        
        async function downloadRegeneratedResults() {
            await downloadArchive(true);
        }
        
        async function downloadTranslatedResults() {
            if (!currentRequestId) {
                alert('Нет активного запроса. Сначала сгенерируйте проект.');
                return;
            }
            
            try {
                const token = localStorage.getItem('auth_token');
                if (!token) {
                    alert('Требуется авторизация');
                    return;
                }
                
                // Скачиваем ZIP архив с переведенным README, диаграммами и файлами данных
                const response = await fetch(`${API_URL}/download/translated/${currentRequestId}`, {
                    headers: getAuthHeaders()
                });
                
                if (!response.ok) {
                    if (response.status === 404) {
                        alert('Переведенный README не найден. Убедитесь, что генерация завершена и перевод доступен.');
                        return;
                    }
                    throw new Error(`Ошибка получения архива: ${response.statusText}`);
                }
                
                // Получаем имя файла из заголовка Content-Disposition
                const contentDisposition = response.headers.get('Content-Disposition');
                let filename = `README_translated_${currentRequestId}.zip`;
                if (contentDisposition) {
                    const filenameMatch = contentDisposition.match(/filename="?(.+)"?/);
                    if (filenameMatch) {
                        filename = filenameMatch[1];
                    }
                }
                
                // Создаем blob из ответа и скачиваем
                const blob = await response.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
            } catch (error) {
                console.error('Ошибка скачивания переведенного архива:', error);
                alert(`Ошибка скачивания: ${error.message}`);
            }
        }

        // ===================== Модуль «Перевод README» =====================

        async function downloadArchive(includeRegenerated = false) {
            if (!currentRequestId) {
                alert('Нет активного запроса. Сначала сгенерируйте проект.');
                return;
            }

            try {
                const token = localStorage.getItem('auth_token');
                const url = `${API_URL}/download/${currentRequestId}${includeRegenerated ? '?include_regenerated=true' : ''}`;
                const response = await fetch(url, {
                    headers: {
                        'Authorization': `Bearer ${token}`
                    }
                });

                if (!response.ok) {
                    const detail = await response.text().catch(() => response.statusText);
                    throw new Error(detail || `Ошибка ${response.status}`);
                }

                const blob = await response.blob();
                const disposition = response.headers.get('Content-Disposition') || '';
                const fileNameMatch = disposition.match(/filename="?([^"]+)"?/i);
                const fallbackName = includeRegenerated ? `contentgen_${currentRequestId}_regen.zip` : `contentgen_${currentRequestId}.zip`;
                const fileName = fileNameMatch ? fileNameMatch[1] : fallbackName;

                const urlBlob = window.URL.createObjectURL(blob);
                const link = document.createElement('a');
                link.href = urlBlob;
                link.download = fileName;
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
                window.URL.revokeObjectURL(urlBlob);
            } catch (error) {
                console.error('Ошибка при скачивании архива:', error);
                alert('Ошибка при скачивании архива: ' + error.message);
            }
        }
        // Делегирование событий для кнопок фильтров метрик
        // Используем делегирование на уровне документа, чтобы обработчики работали даже после перерисовки
        (function() {
            document.addEventListener('click', function(e) {
                // Проверяем, что клик был по кнопке фильтра
                const btn = e.target.closest('.metrics-filter-btn');
                if (btn) {
                    e.preventDefault();
                    e.stopPropagation();
                    const filter = btn.getAttribute('data-filter');
                    if (filter) {
                        window.filterMetrics?.(filter);
                    }
                }
            });
        })();
        
        // Делегирование событий для чекбокса "Показать описание"
        // Используем делегирование на уровне документа, чтобы обработчик работал даже после перерисовки
        (function() {
            document.addEventListener('change', function(e) {
                // Проверяем, что изменение было в чекбоксе "Показать описание"
                if (e.target && e.target.id === 'toggleDescription' && e.target.type === 'checkbox') {
                    window.toggleDescriptionColumn?.();
                }
                if (e.target && e.target.id === 'methodologyHumanReview' && e.target.type === 'checkbox') {
                    setWorkflowProfileState(e.target.checked ? 'methodology' : 'standard');
                    updateRegenerationAvailability();
                }
            });
        })();
        
        async function handleLogout() {
            if (!confirm('Вы уверены, что хотите выйти?')) {
                return;
            }
            
            try {
                const token = localStorage.getItem('auth_token');
                if (token) {
                    await fetch(`${API_URL}/logout`, {
                        method: 'POST',
                        headers: {
                            'Authorization': `Bearer ${token}`
                        }
                    });
                }
            } catch (error) {
                console.error('Ошибка при выходе:', error);
            } finally {
                // Очищаем localStorage и sessionStorage
                localStorage.removeItem('auth_token');
                localStorage.removeItem('user_id');
                localStorage.removeItem('username');
                localStorage.removeItem('session_id');
                clearGenerationState(); // Очищаем состояние генерации
                window.location.href = '/';
            }
        }

        function goToMainMenu() {
            window.location.href = '/app';
        }
        
        function showTab(tabName, clickedElement) {
            console.log('showTab вызвана:', tabName, clickedElement);
            if (tabName === 'regen' && !isProjectRegenerationEnabled()) {
                tabName = 'readme';
                clickedElement = document.querySelector('.result-tabs .tab[onclick*="readme"]');
            }
            
            // Скрываем все вкладки и их контент
            document.querySelectorAll('.tab').forEach(tab => {
                tab.classList.remove('active');
            });
            
            document.querySelectorAll('.tab-content').forEach(content => {
                content.classList.remove('active');
                content.style.display = 'none'; // Явно скрываем все
            });
            
            // Показываем выбранную вкладку
            if (clickedElement) {
                clickedElement.classList.add('active');
            } else {
                // Если элемент не передан, находим по тексту
                document.querySelectorAll('.tab').forEach(tab => {
                    if (tab.textContent.includes(tabName) || (tab.onclick && tab.onclick.toString().includes(tabName))) {
                        tab.classList.add('active');
                    }
                });
            }
            
            // Показываем контент вкладки, если он существует
            const tabContent = document.getElementById(tabName);
            if (tabContent) {
                tabContent.classList.add('active');
                tabContent.style.display = 'block'; // Явно показываем выбранную
                console.log('Показан контент вкладки:', tabName);
                
                // Если открыта вкладка "Критерии", обновляем отображение критериев
                if (tabName === 'metrics') {
                    console.log('📈 Вкладка критериев открыта, обновляем отображение', {
                        hasOriginal: !!window.checkerRubric,
                        hasImproved: !!window.improvedRubric,
                        hasMainOriginal: !!originalRubric,
                        hasMainRegenerated: !!regeneratedRubric,
                        currentMetricsVersion: currentMetricsVersion,
                        updateCheckerMetricsDisplayAvailable: typeof window.updateCheckerMetricsDisplay === 'function'
                    });
                    
                    // Для checker.html
                    if (typeof window.updateCheckerMetricsDisplay === 'function') {
                        setTimeout(() => {
                            console.log('🔄 Вызываем updateCheckerMetricsDisplay из showTab');
                            window.updateCheckerMetricsDisplay();
                        }, 100);
                    } 
                    // Для основной страницы генератора
                    else {
                        // Определяем, какую версию метрик показывать
                        const version = currentMetricsVersion || 'original';
                        const containerOriginal = document.getElementById('metricsContentOriginal');
                        const containerRegen = document.getElementById('metricsContentRegen');
                        
                        if (version === 'original' && originalRubric && containerOriginal) {
                            console.log('🔄 Обновляем оригинальные метрики при открытии вкладки');
                            displayMetrics(originalRubric, 'metricsContentOriginal');
                        } else if (version === 'regenerated' && regeneratedRubric && containerRegen) {
                            console.log('🔄 Обновляем перегенерированные метрики при открытии вкладки');
                            displayMetrics(regeneratedRubric, 'metricsContentRegen');
                        } else if (originalRubric && containerOriginal) {
                            // Если версия не определена, показываем оригинальные
                            console.log('🔄 Показываем оригинальные метрики по умолчанию');
                            displayMetrics(originalRubric, 'metricsContentOriginal');
                        }
                    }
                }

                if (document.body.classList.contains('generation-completed')) {
                    setCompletedChrome(tabName);
                    if (tabName === 'readme' && currentMarkdown) {
                        renderResultReadme(currentMarkdown);
                    }
                    if (tabName === 'regen') {
                        window.renderRegenerationReadme?.();
                    }
                }
                
                // Дополнительная проверка: убеждаемся, что все остальные скрыты
                document.querySelectorAll('.tab-content').forEach(content => {
                    if (content.id !== tabName && content.style.display !== 'none') {
                        console.warn(`Вкладка ${content.id} не скрыта, исправляем...`);
                        content.style.display = 'none';
                        content.classList.remove('active');
                    }
                });
            } else {
                console.warn(`Контент вкладки "${tabName}" не найден`);
            }
        }
        
        // Экспортируем все функции в глобальную область видимости для использования в onclick
        // Это нужно, чтобы функции были доступны из inline обработчиков onclick
        // Функции уже определены выше, просто делаем их доступными глобально
        try {
            // Прямое присваивание функций в window (они уже определены в области видимости IIFE)
            window.toggleExpander = toggleExpander;
            window.generateContent = generateContent;
            window.clearForm = clearForm;
            window.clearGeneration = clearGeneration;
            window.showTab = showTab;
            window.handleLogout = handleLogout;
            window.regenerateContent = regenerateContent;
            window.downloadResults = downloadResults;
            window.downloadRegeneratedResults = downloadRegeneratedResults;
            window.downloadTranslatedResults = downloadTranslatedResults;
            window.handleTranslationFileSelect = handleTranslationFileSelect;
            window.translateReadme = translateReadme;
            window.downloadTranslatedMarkdown = downloadTranslatedMarkdown;
            window.downloadTranslatedSubtitles = downloadTranslatedSubtitles;
            window.resetTranslationState = resetTranslationState;
            window.fillCommentsFromFailedCriteria = fillCommentsFromFailedCriteria;
            window.switchMetricsVersion = switchMetricsVersion;
            window.switchReportVersion = switchReportVersion;
            window.clearRegeneration = clearRegeneration;
            window.addThematicBlock = addThematicBlock;
            window.toggleBonusWish = toggleBonusWish;
            window.toggleGroupSize = toggleGroupSize;
            window.checkReadme = checkReadme;
            window.handleReadmeFileSelect = handleReadmeFileSelect;
            window.goToMainMenu = goToMainMenu;
            window.insertRegenerationTemplate = insertRegenerationTemplate;
            window.getAuthHeaders = getAuthHeaders;
            window.displayResults = displayResults;
            window.updateVersionButtons = updateVersionButtons;
            window.updateRegenerationAvailability = updateRegenerationAvailability;
            window.getWorkflowProfileState = getWorkflowProfileState;
            window.getWorkflowCapability = getWorkflowCapability;
            window.cancelGeneration = cancelGeneration;
            window.showMethodologyReviewActions = showMethodologyReviewActions;
            window.hideMethodologyReviewActions = hideMethodologyReviewActions;
            window.renderMethodologyPanel = renderMethodologyPanel;
            window.startTimer = startTimer;
            window.stopGenerationTracking = stopGenerationTracking;
            window.pollGenerationStatus = pollGenerationStatus;
            window.updateCurrentAgent = updateCurrentAgent;
            window.setGenerationStatusActive = setGenerationStatusActive;
            window.showCancelButton = showCancelButton;
            window.hideCancelButton = hideCancelButton;
            window.displayMarkdown = displayMarkdown;
            window.renderMarkdownPreview = renderMarkdownPreview;
            window.normalizeMarkdownForDisplay = normalizeMarkdownForDisplay;
            window.renderMermaidDiagrams = renderMermaidDiagrams;
            window.setReadmeRenderMode = setReadmeRenderMode;
            window.compareCurrentResult = compareCurrentResult;
            window.openRegenerationFromMetrics = openRegenerationFromMetrics;
            
            // Curriculum (УП) functions
            window.handleCurriculumUpload = handleCurriculumUpload;
            window.populateCurriculumBlocks = populateCurriculumBlocks;
            window.onCurriculumBlockChange = onCurriculumBlockChange;
            window.onCurriculumProjectChange = onCurriculumProjectChange;
            window.onDirectionChange = onDirectionChange;
            
            console.log('✅ Все функции экспортированы в window');
        } catch (error) {
            console.error('❌ Ошибка при экспорте функций:', error);
        }
        
        // Инициализация при загрузке
        window.addEventListener('DOMContentLoaded', async () => {
            try {
                const hasGeneratorUI = Boolean(document.getElementById('generateBtn'));
                const hasCheckerUI = Boolean(document.getElementById('checkBtn'));
                if (hasGeneratorUI) {
                    window.MethodologyAssistantChat?.initialize();
                    window.initializeStorytellingTypeHelp?.();
                    const displayName = localStorage.getItem('username') || localStorage.getItem('email') || 'Пользователь';
                    const generatorUserName = document.getElementById('generatorUserName');
                    const generatorUserInitials = document.getElementById('generatorUserInitials');
                    if (generatorUserName) generatorUserName.textContent = displayName;
                    if (generatorUserInitials) {
                        const shortName = displayName.replace(/@.*$/, '').split(/[.\s_-]+/).filter(Boolean);
                        generatorUserInitials.textContent = (shortName.length >= 2 ? `${shortName[0][0]}${shortName[1][0]}` : displayName.slice(0, 2)).toUpperCase();
                    }
                    loadThematicBlocks();
                    updateThematicBlockSelect();
                    await loadGenerationState();
                    setTimeout(() => {
                        if (typeof window.toggleGroupSize === 'function') {
                            window.toggleGroupSize();
                        } else if (typeof toggleGroupSize === 'function') {
                            toggleGroupSize();
                        }
                        const directionEl = document.getElementById('direction');
                        const thematicEl = document.getElementById('thematicBlock');
                        if (directionEl && thematicEl && !thematicEl.value && directionEl.value && directionEl.value !== 'ADD') {
                            thematicEl.value = directionEl.value;
                        }
                        updateRegenerationAvailability();
                    }, 100);
                }
                if (hasCheckerUI) {
                    // Ничего дополнительного, но убеждаемся, что функции экспорта доступны
                }
                
                // Проверка доступности функций
                console.log('DOM загружен, проверка функций:');
                console.log('toggleExpander:', typeof window.toggleExpander);
                console.log('generateContent:', typeof window.generateContent);
                console.log('clearForm:', typeof window.clearForm);
                console.log('showTab:', typeof window.showTab);
                console.log('handleLogout:', typeof window.handleLogout);
                
                // Проверяем наличие expander элементов
                const expanders = document.querySelectorAll('.expander');
                console.log(`Найдено expander элементов: ${expanders.length}`);
                
                // Проверяем наличие кнопок
                const buttons = document.querySelectorAll('button[onclick]');
                console.log(`Найдено кнопок с onclick: ${buttons.length}`);
            } catch (error) {
                console.error('Ошибка при инициализации:', error);
            }
        });
        
        // Экспортируем дополнительные функции для использования в HTML (checker.html)
        // Основные функции уже экспортированы выше
        window.clearCheckerResults = clearCheckerResults;
        window.restoreCheckerResults = restoreCheckerResults;
        })(); // Закрываем IIFE


