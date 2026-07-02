// Generator progress/run-view controller.
// Reads main.js state through ContentGenGenerationRuntime instead of closing over it.

        function getGenerationRuntime() {
            return window.ContentGenGenerationRuntime || {};
        }

        function getGenerationState() {
            const runtime = getGenerationRuntime();
            return typeof runtime.getState === 'function' ? runtime.getState() : {};
        }

        function callGenerationRuntime(name, ...args) {
            const runtime = getGenerationRuntime();
            return typeof runtime[name] === 'function' ? runtime[name](...args) : undefined;
        }

function timelineStageFromPhase(phase) {
            const normalized = String(phase || '').trim();
            if (!normalized) return null;
            if (['initialization', 'context', 'task_planning', 'title', 'title_annotation'].includes(normalized)) return 'context';
            if (['skeleton', 'intro_rules', 'structural_preflight'].includes(normalized)) return 'skeleton';
            if (['theory', 'definitions', 'theory_checks', 'practice', 'dataset_generation'].includes(normalized)) return 'theory';
            if (['quality', 'global_quality', 'evaluation', 'readme_check', 'validation'].includes(normalized)) return 'quality';
            if (['finalize', 'completion'].includes(normalized)) return 'assembly';
            return null;
        }

        function updateGenerationTimeline(phase, status = 'in_progress') {
            const timeline = document.getElementById('generationTimeline');
            if (!timeline) return;
            const stages = ['context', 'skeleton', 'theory', 'quality', 'assembly'];
            const activeStage = timelineStageFromPhase(phase) || (status === 'completed' ? 'assembly' : 'context');
            const activeIndex = Math.max(0, stages.indexOf(activeStage));
            timeline.querySelectorAll('.s21-tl-row').forEach((row) => {
                const stage = row.getAttribute('data-stage');
                const index = stages.indexOf(stage);
                row.classList.remove('done', 'now', 'pending');
                if (status === 'completed' || index < activeIndex) {
                    row.classList.add('done');
                } else if (index === activeIndex && status !== 'failed' && status !== 'cancelled' && status !== 'needs_review') {
                    row.classList.add('now');
                } else {
                    row.classList.add('pending');
                }
                const time = row.querySelector('.s21-tl-time');
                if (time) {
                    if (status === 'completed' || index < activeIndex) time.textContent = 'готово';
                    else if (index === activeIndex && status !== 'failed' && status !== 'cancelled' && status !== 'needs_review') time.textContent = 'в работе';
                    else time.textContent = 'ожидает';
                }
            });
        }

        function showCompactGenerationProgress(message = 'Генерация проекта...', options = {}) {
            const state = getGenerationState();
            const generationLogs = document.getElementById('generationLogs');
            const logContent = document.getElementById('logContent');
            if (generationLogs) {
                generationLogs.style.display = 'block';
            }
            setGenerationStatusActive(true);
            updateGenerationTimeline(options.phase || state.lastKnownGenerationPhase, options.status || 'in_progress');
            showGenerationRunView(state.currentSeed || {}, {
                ...options,
                message,
                phase: options.phase || state.lastKnownGenerationPhase || 'initialization',
                status: options.status || state.currentGenerationStatus || 'in_progress'
            });
            if (!logContent) return;

            logContent.innerHTML = '';
            let progressBarId = null;
            let spinnerId = null;
            if (window.loading) {
                progressBarId = window.loading.createProgressBar('logContent', 'Прогресс генерации');
                spinnerId = window.loading.showSpinner('logContent', message);
            } else {
                logContent.innerHTML = `<div class="generation-activity"><span class="generation-activity-dot"></span><span>${message}</span></div>`;
            }

            window.currentProgressBarId = progressBarId;
            window.currentSpinnerId = spinnerId;
            if (options.agent) {
                const agentElement = document.getElementById('currentAgent');
                if (agentElement) {
                    agentElement.textContent = options.agent;
                }
            }
            if (window.loading && progressBarId && Number(options.progress || 0) > 0) {
                window.loading.updateProgress(progressBarId, options.progress);
            }
        }

        /**
         * Вычисляет процент прогресса на основе фазы генерации
         */
        function calculateProgressFromPhase(phase) {
            const phaseProgressMap = {
                'initialization': 5,
                'context': 15,
                'task_planning': 25,
                'title': 30,
                'title_annotation': 30,
                'skeleton': 35,
                'intro_rules': 38,
                'structural_preflight': 42,
                'theory': 50,
                'definitions': 55,
                'theory_checks': 58,
                'practice': 65,
                'dataset_generation': 72,
                'quality': 75,
                'global_quality': 80,
                'evaluation': 88,
                'finalize': 95,
                'readme_check': 98,
                'completion': 100,
                'validation': 92,
                'methodology_review': 0,
            };
            
            return phaseProgressMap[phase] || 0;
        }

        function progressFromCheckpointStage(stage) {
            const normalized = {
                title: 'title_annotation',
                annotation: 'title_annotation',
                structure: 'skeleton',
                skeleton: 'skeleton',
                theory: 'theory_checks',
                practice: 'practice',
                dataset: 'dataset_generation',
                materials: 'dataset_generation',
                quality: 'global_quality',
                evaluation: 'evaluation',
                final: 'global_quality'
            }[String(stage || '').trim()] || String(stage || '').trim();
            return calculateProgressFromPhase(normalized);
        }

        const GENERATION_RUN_STAGES = [
            {
                id: 'context',
                title: 'Анализ контекста',
                subtitle: 'Проверяем учебный план, результаты обучения, соседние проекты и ограничения.'
            },
            {
                id: 'planning',
                title: 'Планирование практики',
                subtitle: 'Определяем количество задач, сложность и цепочку артефактов.'
            },
            {
                id: 'skeleton',
                title: 'Каркас README',
                subtitle: 'Собираем структуру, содержание и навигацию будущего документа.'
            },
            {
                id: 'theory',
                title: 'Генерация теории',
                subtitle: 'Пишем теоретические разделы, примеры и визуальные блоки.'
            },
            {
                id: 'practice',
                title: 'Генерация практики',
                subtitle: 'Собираем задания, p2p-критерии, материалы и ожидаемые результаты.'
            },
            {
                id: 'quality',
                title: 'Проверка качества',
                subtitle: 'Проверяем структуру, связность, полноту и didactics-контракты.'
            },
            {
                id: 'evaluation',
                title: 'Оценка по критериям',
                subtitle: 'Сверяем README с rubric и фиксируем замечания.'
            },
            {
                id: 'assembly',
                title: 'Сборка результата',
                subtitle: 'Готовим README, отчёты и архив для скачивания.'
            }
        ];

        function escapeHtmlSafe(value) {
            const text = String(value ?? '');
            if (window.sanitize?.escapeHtml) {
                return window.sanitize.escapeHtml(text);
            }
            return text
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#039;');
        }

        function getSelectText(id) {
            const element = document.getElementById(id);
            if (!element || !element.selectedOptions || !element.selectedOptions.length) {
                return element?.value || '';
            }
            return element.selectedOptions[0].textContent.trim();
        }

        function setTextContent(id, value) {
            const element = document.getElementById(id);
            if (element) {
                element.textContent = value || '—';
            }
        }

        function languageLabel(code) {
            const labels = {
                ru: 'RU · Русский',
                en: 'EN · Английский',
                kg: 'KG · Киргизский',
                uz: 'UZ · Узбекский'
            };
            return labels[code] || code || '—';
        }

        function projectTypeLabel(seed) {
            const type = seed?.project_type === 'group' ? 'Групповой' : 'Индивидуальный';
            const audience = seed?.audience_level || getValueOrFallback('audienceLevel', '');
            const group = seed?.project_type === 'group' && seed?.group_size ? ` · ${seed.group_size} чел.` : '';
            return `${type}${audience ? ` · ${audience}` : ''}${group}`;
        }

        function getValueOrFallback(id, fallback = '') {
            const element = document.getElementById(id);
            return element ? element.value : fallback;
        }

        function renderGenerationRunSnapshot(seed = getGenerationState().currentSeed || {}) {
            const curriculumFileName = document.getElementById('curriculumFileName')?.textContent?.trim();
            const curriculumProject = getSelectText('curriculumProject');
            const blockText = getSelectText('curriculumBlock') || seed.thematic_block || getSelectText('direction');
            const tasks = seed.tasks_count || getValueOrFallback('tasksCount') || 'авто';
            const bonus = seed.bonus_wish !== null && seed.bonus_wish !== undefined && seed.bonus_wish !== ''
                ? ' + бонус'
                : (getChecked('generateBonus') ? ' + бонус' : '');

            setTextContent('generationRunStartedAt', new Date((getGenerationState().generationStartTime) || Date.now()).toLocaleTimeString('ru-RU', {
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit'
            }));
            setTextContent('runParamCurriculum', curriculumFileName && !curriculumFileName.includes('Поддерживается') ? curriculumFileName : 'ручной ввод');
            setTextContent('runParamDirection', getSelectText('direction') || seed.direction || '—');
            setTextContent('runParamBlock', blockText || '—');
            setTextContent('runParamProject', curriculumProject && !curriculumProject.includes('Выберите') ? curriculumProject : (seed.platform_name || seed.title_seed || '—'));
            setTextContent('runParamTitle', seed.title_seed || getValueOrFallback('titleSeed') || '—');
            setTextContent('runParamType', projectTypeLabel(seed));
            setTextContent('runParamTasks', `${tasks}${bonus}`);
            const profile = getGenerationState().workflowProfile || {};
            const capabilities = getGenerationState().workflowCapabilities || profile.capabilities || {};
            setTextContent(
                'runParamMethodology',
                capabilities.stage_review || seed.methodology_human_review || getChecked('methodologyHumanReview')
                    ? 'Включена'
                    : 'Обычный режим'
            );
        }

        function setGeneratorBrand(step, mark, sub) {
            const badge = document.getElementById('generatorBrandBadge');
            if (badge) {
                badge.textContent = step;
                badge.setAttribute('data-step', step);
            }
            setTextContent('generatorBrandMark', mark);
            setTextContent('generatorBrandSub', sub || 'учебных проектов · v 2.4');
        }

        function setGeneratorSubbar({ backText, backHref, title, statusText, statusClass = 'info', rightHtml = '' } = {}) {
            const back = document.getElementById('generatorBackLink');
            if (back) {
                back.textContent = backText || '← Главное меню';
                back.href = backHref || '/app';
                back.onclick = null;
            }
            setTextContent('generatorSubbarTitle', title || 'Генерация README');
            const status = document.getElementById('generatorSubbarStatus');
            if (status) {
                status.className = `badge ${statusClass}`;
                status.textContent = statusText || 'ЧЕРНОВИК';
                status.style.display = statusText === null ? 'none' : 'inline-flex';
            }
            const right = document.getElementById('generatorSubbarRight');
            if (right) {
                if (rightHtml) {
                    right.innerHTML = rightHtml;
                } else {
                    right.textContent = '✓ Автосохранено · локальное состояние';
                }
            }
        }

        function resetGeneratorChrome() {
            document.body.classList.remove('generation-running', 'generation-stage-review', 'generation-completed', 'generation-metrics-view');
            setGeneratorBrand('03.1', 'ФОРМА ПАРАМЕТРОВ', 'учебных проектов · v 2.4');
            setGeneratorSubbar();
        }

        function formatGenerationElapsed() {
            const { generationStartTime } = getGenerationState();
            if (!generationStartTime) return '';
            const elapsed = Math.max(0, Math.floor((Date.now() - Number(generationStartTime)) / 1000));
            const minutes = String(Math.floor(elapsed / 60)).padStart(2, '0');
            const seconds = String(elapsed % 60).padStart(2, '0');
            return `${minutes}:${seconds}`;
        }

        function extractMarkdownTitle(markdown) {
            const match = String(markdown || '').match(/^#\s+(.+)$/m);
            return match ? match[1].replace(/^#+\s*/, '').trim() : '';
        }

        function getResultDisplayTitle(data = null) {
            const state = getGenerationState();
            const result = data?.result || state.currentResult || {};
            const seed = data?.seed || state.currentSeed || {};
            const code = seed.platform_name || seed.project_code || result.platform_name || result.project_code || '';
            const title = seed.title_seed || result.title_seed || result.title || extractMarkdownTitle(result.markdown || state.currentMarkdown);
            if (code && title && !String(title).includes(code)) return `${code} · ${title}`;
            return title || code || 'Итоговый README';
        }

        function runStageFromPhase(phase, methodology = null) {
            const checkpointStage = methodology?.checkpoint?.stage || methodology?.checkpoint?.id || '';
            const raw = String(phase || checkpointStage || '').trim();
            const normalized = {
                initialization: 'context',
                context: 'context',
                task_planning: 'planning',
                title: 'skeleton',
                title_annotation: 'skeleton',
                skeleton: 'skeleton',
                intro_rules: 'skeleton',
                structural_preflight: 'skeleton',
                theory: 'theory',
                definitions: 'theory',
                theory_checks: 'theory',
                practice: 'practice',
                dataset_generation: 'practice',
                quality: 'quality',
                global_quality: 'quality',
                validation: 'evaluation',
                evaluation: 'evaluation',
                readme_check: 'evaluation',
                finalize: 'assembly',
                completion: 'assembly',
                final: 'assembly'
            };
            return normalized[raw] || 'context';
        }

        function phaseFromWorkflow(workflow, methodology = null) {
            if (!workflow || typeof workflow !== 'object') {
                return null;
            }
            const node = String(workflow.current_node || workflow.last_completed_node || workflow.resume_from_node || '').trim();
            const mapped = {
                context: 'context',
                task_planning: 'task_planning',
                title_annotation: 'title_annotation',
                skeleton: 'skeleton',
                theory: 'theory',
                practice: 'practice',
                global_quality: 'global_quality',
                quality: 'global_quality',
                evaluation: 'evaluation',
                finalize: 'finalize',
            }[node];
            if (mapped) return mapped;
            const checkpointStage = methodology?.checkpoint?.stage || methodology?.checkpoint?.id;
            return checkpointStage ? runStageFromPhase(checkpointStage, methodology) : null;
        }

        function progressFromWorkflow(workflow, status = getGenerationState().currentGenerationStatus) {
            if (!workflow || typeof workflow !== 'object') return 0;
            if (status === 'completed' || workflow.status === 'completed') return 100;
            const total = Number(workflow.progress_total || 0);
            const current = Number(workflow.progress_current || 0);
            if (total <= 0) return 0;
            return Math.max(1, Math.min(99, Math.round((current / total) * 100)));
        }

        function agentFromWorkflow(workflow) {
            if (!workflow || typeof workflow !== 'object') return '';
            const metadata = workflow.metadata && typeof workflow.metadata === 'object' ? workflow.metadata : {};
            if (metadata.current_node_name) return String(metadata.current_node_name);
            const checkpoints = Array.isArray(workflow.checkpoints) ? workflow.checkpoints : [];
            const last = checkpoints.length ? checkpoints[checkpoints.length - 1] : null;
            return String(last?.node_name || workflow.current_node || workflow.last_completed_node || '');
        }

        function workflowUiOptions(statusPayload = {}) {
            const workflow = statusPayload.workflow || null;
            const phase = phaseFromWorkflow(workflow, statusPayload.methodology || null);
            const progress = progressFromWorkflow(workflow, statusPayload.status);
            const agent = agentFromWorkflow(workflow);
            return {
                workflow,
                phase,
                progress,
                agent
            };
        }

        function runStageToChangeTarget(stage) {
            if (stage === 'theory') return 'theory';
            if (stage === 'practice') return 'practice';
            if (stage === 'skeleton') return 'skeleton';
            if (stage === 'context' || stage === 'planning') return 'structure';
            return 'final';
        }

        function showGenerationRunView(seed = getGenerationState().currentSeed || {}, options = {}) {
            const state = getGenerationState();
            const runView = document.getElementById('generationRunView');
            if (!runView) return;
            document.body.classList.add('generation-running');
            document.body.classList.remove('generation-completed', 'generation-metrics-view');
            setGeneratorBrand('03.2', 'ПРОГРЕСС ПАЙПЛАЙНА', `${Math.max(1, Math.min(GENERATION_RUN_STAGES.length, 1 + GENERATION_RUN_STAGES.findIndex(stage => stage.id === runStageFromPhase(options.phase || state.lastKnownGenerationPhase || 'initialization'))))}/${GENERATION_RUN_STAGES.length} + ЧАТ МЕТОДОЛОГА`);
            setGeneratorSubbar({
                backText: '← Главное меню',
                backHref: '/app',
                title: 'Генерация README',
                statusText: '● ВЫПОЛНЯЕТСЯ',
                statusClass: 'info',
                rightHtml: '<button class="btn btn-secondary btn-sm" type="button" onclick="cancelGeneration()">▪ Аварийная остановка</button>'
            });
            renderGenerationRunSnapshot(seed);
            runView.style.display = 'block';
            const noResults = document.getElementById('noResults');
            const resultsArea = document.getElementById('resultsArea');
            const reviewWorkspace = document.getElementById('methodologyReviewWorkspace');
            if (noResults) noResults.style.display = 'none';
            if (resultsArea) resultsArea.style.display = 'none';
            if (options.status === 'needs_review') {
                document.body.classList.add('generation-stage-review');
                runView.style.display = 'none';
                if (reviewWorkspace) reviewWorkspace.style.display = 'block';
                setGeneratorBrand('03.2', 'РЕЗУЛЬТАТ ЭТАПА', `${Math.max(1, Math.min(GENERATION_RUN_STAGES.length, 1 + GENERATION_RUN_STAGES.findIndex(stage => stage.id === runStageFromPhase(options.phase || state.lastKnownGenerationPhase || 'methodology_review', options.methodology || null))))}/${GENERATION_RUN_STAGES.length} + ЧАТ МЕТОДОЛОГА`);
                setGeneratorSubbar({
                    backText: '← Главное меню',
                    backHref: '/app',
                    title: 'Проверка результата этапа',
                    statusText: '● ОЖИДАЕТ МЕТОДОЛОГА',
                    statusClass: 'warn',
                    rightHtml: '<button class="btn btn-secondary btn-sm" type="button" onclick="cancelGeneration()">▪ Аварийная остановка</button>'
                });
            } else {
                document.body.classList.remove('generation-stage-review');
                runView.style.display = 'block';
                if (reviewWorkspace) reviewWorkspace.style.display = 'none';
            }
            updateGenerationRunProgress(options.phase || state.lastKnownGenerationPhase || 'initialization', options.status || state.currentGenerationStatus || 'in_progress', options);
            callGenerationRuntime('showMethodologyAssistantChat', options.status || state.currentGenerationStatus || 'in_progress');
        }

        function finishGenerationRun(status, message = '') {
            document.body.classList.remove('generation-running', 'generation-stage-review');
            const runView = document.getElementById('generationRunView');
            if (runView) {
                runView.style.display = 'none';
            }
            if (status === 'completed') {
                callGenerationRuntime('appendAssistantChatMessage', 'assistant', message || 'Генерация завершена. Итоговый README открыт в главном окне; можно продолжить обсуждение здесь.');
            } else if (status === 'failed') {
                callGenerationRuntime('appendAssistantChatMessage', 'assistant', message || 'Генерация остановилась с ошибкой. Проверьте лог и входные параметры.');
            } else if (status === 'cancelled') {
                callGenerationRuntime('appendAssistantChatMessage', 'assistant', message || 'Генерация остановлена. Комментарии в этом чате останутся до очистки страницы.');
            }
        }

        function updateGenerationRunProgress(phase, status = 'in_progress', options = {}) {
            const state = getGenerationState();
            const runView = document.getElementById('generationRunView');
            if (!runView) return;

            const stageId = runStageFromPhase(phase, options.methodology || null);
            const stageIndex = Math.max(0, GENERATION_RUN_STAGES.findIndex(stage => stage.id === stageId));
            const activeStage = GENERATION_RUN_STAGES[stageIndex] || GENERATION_RUN_STAGES[0];
            const isPaused = status === 'needs_review';
            const isCompleted = status === 'completed';
            const progress = isCompleted
                ? 100
                : Math.max(Number(options.progress || 0), state.lastKnownGenerationProgress || 0, calculateProgressFromPhase(phase) || Math.round((stageIndex / (GENERATION_RUN_STAGES.length - 1)) * 100));
            const clampedProgress = Math.max(0, Math.min(100, progress));

            const ring = document.getElementById('generationRunRing');
            if (ring) ring.style.setProperty('--run-progress', `${clampedProgress}%`);
            setTextContent('generationRunPercent', `${clampedProgress}%`);
            setTextContent('generationRunStageIndex', String(stageIndex + 1).padStart(2, '0'));
            setTextContent('generationRunStageTotal', String(GENERATION_RUN_STAGES.length));
            setTextContent('generationRunTitle', isPaused ? `Ожидание методолога: ${activeStage.title}` : activeStage.title);
            setTextContent('generationRunSubtitle', options.message || activeStage.subtitle);
            setTextContent('generationRunRemaining', isPaused ? 'ожидает решения' : 'осталось ~ 6–15 мин');

            document.querySelectorAll('#generationRunTimeline .generation-pipeline-step').forEach((row, index) => {
                row.classList.remove('done', 'now', 'pending', 'paused', 'skipped');
                const statusNode = row.querySelector('em');
                if (isCompleted || index < stageIndex) {
                    row.classList.add('done');
                    if (statusNode) statusNode.textContent = 'готово';
                } else if (index === stageIndex) {
                    row.classList.add(isPaused ? 'paused' : 'now');
                    if (statusNode) statusNode.textContent = isPaused ? 'пауза' : 'в работе';
                } else {
                    row.classList.add('pending');
                    if (statusNode) statusNode.textContent = 'ожидает';
                }
            });

            const checkpoint = document.getElementById('generationRunCheckpoint');
            const checkpointText = document.getElementById('generationRunCheckpointText');
            if (checkpoint) {
                checkpoint.style.display = isPaused ? 'grid' : 'none';
            }
            if (checkpointText) {
                checkpointText.textContent = options.message || 'Пайплайн ожидает решения методолога. Комментарий можно отправить через чат.';
            }

            setTextContent('generationRunLogContent', options.agent || state.lastKnownGenerationAgent || activeStage.title);
            callGenerationRuntime('updateAssistantChatStatus', status, stageId);
        }

        if (typeof window !== 'undefined') {
            Object.assign(window, {
                updateGenerationTimeline,
                showCompactGenerationProgress,
                calculateProgressFromPhase,
                progressFromCheckpointStage,
                escapeHtmlSafe,
                getSelectText,
                setTextContent,
                languageLabel,
                projectTypeLabel,
                getValueOrFallback,
                renderGenerationRunSnapshot,
                setGeneratorBrand,
                setGeneratorSubbar,
                resetGeneratorChrome,
                formatGenerationElapsed,
                extractMarkdownTitle,
                getResultDisplayTitle,
                runStageFromPhase,
                phaseFromWorkflow,
                progressFromWorkflow,
                agentFromWorkflow,
                workflowUiOptions,
                runStageToChangeTarget,
                showGenerationRunView,
                finishGenerationRun,
                updateGenerationRunProgress,
            });
        }

