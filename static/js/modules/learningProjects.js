// Operational view for projects generated from persisted curriculum plans.

(function () {
    const API_URL = `${window.location.origin}/api/v1`;
    const ACTIVE_STATUSES = new Set(['pending', 'in_progress', 'needs_review', 'resuming']);
    const STATUS_LABELS = {
        not_started: 'Не начат',
        blocked: 'Заблокирован',
        pending: 'В очереди',
        in_progress: 'В работе',
        needs_review: 'На проверке',
        resuming: 'Возобновление',
        completed: 'Готов',
        failed: 'Ошибка',
        cancelled: 'Остановлен',
        interrupted: 'Прерван',
    };

    let currentProjects = [];
    let currentPlanId = '';
    let currentPlanReady = false;

    function getAuthHeaders() {
        if (window.ContentGenAuth?.getAuthHeaders) {
            return window.ContentGenAuth.getAuthHeaders();
        }
        const token = localStorage.getItem('auth_token');
        return token ? { Authorization: `Bearer ${token}` } : {};
    }

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function setText(id, value) {
        const element = document.getElementById(id);
        if (element) element.textContent = String(value ?? '');
    }

    function setStatus(message, isError = false) {
        const status = document.getElementById('learningProjectsStatus');
        if (!status) return;
        status.textContent = message || '';
        status.classList.toggle('is-error', Boolean(isError));
    }

    function updateUserHeader() {
        const name = localStorage.getItem('username') || localStorage.getItem('email') || 'Пользователь';
        setText('learningProjectsUserName', name);
        setText('learningProjectsUserInitials', name.replace(/@.*$/, '').slice(0, 2).toUpperCase() || 'U');
    }

    async function fetchJson(url) {
        const response = await fetch(url, { headers: getAuthHeaders() });
        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Ошибка загрузки данных' }));
            const detail = error.detail;
            const message = typeof detail === 'string' ? detail : detail?.message || `Ошибка ${response.status}`;
            throw new Error(message);
        }
        return response.json();
    }

    async function loadPlans() {
        const select = document.getElementById('learningProjectPlanSelect');
        if (!select) return;
        setStatus('Загрузка учебных планов...');
        const data = await fetchJson(`${API_URL}/curriculum-projects/plans`);
        const plans = Array.isArray(data.plans) ? data.plans : [];
        select.innerHTML = plans.length
            ? '<option value="">Выберите УП</option>'
            : '<option value="">УП не найдены</option>';
        for (const plan of plans) {
            const option = document.createElement('option');
            option.value = String(plan.source_id || plan.id);
            option.textContent = `${plan.title || `УП #${option.value}`} · ${Number(plan.projects || 0)} проектов`;
            select.appendChild(option);
        }

        const requestedPlanId = new URLSearchParams(window.location.search).get('plan_id');
        if (requestedPlanId && plans.some((plan) => String(plan.source_id || plan.id) === requestedPlanId)) {
            select.value = requestedPlanId;
        } else if (plans.length === 1) {
            select.value = String(plans[0].source_id || plans[0].id);
        }

        if (select.value) {
            await loadPlan(select.value);
        } else {
            setStatus(plans.length ? 'Выберите учебный план.' : 'В базе пока нет учебных планов.', !plans.length);
            renderFilteredProjects();
            updateStats({});
        }
    }

    async function loadPlan(planId) {
        if (!planId) return;
        currentPlanId = String(planId);
        setStatus('Загрузка проектов...');
        const data = await fetchJson(`${API_URL}/curriculum-projects/plans/${encodeURIComponent(planId)}`);
        currentPlanReady = data.readiness?.ready !== false;
        currentProjects = Array.isArray(data.projects)
            ? data.projects.map((project, index) => ({ ...project, _uiIndex: index }))
            : [];
        renderReadiness(data.readiness || {});
        updateStats(data.stats || {});
        renderFilteredProjects();
        updateStatusSummary(data.plan?.title || `УП #${planId}`);
    }

    function updateStats(stats) {
        setText('learningProjectsTotal', stats.total_projects || 0);
        setText('learningProjectsDone', stats.generated || 0);
        setText('learningProjectsActive', stats.in_progress || 0);
        setText('learningProjectsFailed', stats.failed || 0);
    }

    function renderReadiness(readiness) {
        const panel = document.getElementById('learningProjectsReadiness');
        if (!panel) return;
        if (readiness.ready !== false) {
            panel.hidden = true;
            panel.innerHTML = '';
            return;
        }
        const blockers = Array.isArray(readiness.blockers) ? readiness.blockers : [];
        const items = blockers.map((blocker) => {
            const count = Number(blocker.count || 0);
            const suffix = count > 1 ? ` · ${count}` : '';
            const reviewLink = blocker.code === 'open_reviews'
                ? ' <a href="/app/spravochnik/reviews?status=open">Открыть review</a>'
                : '';
            return `<li>${escapeHtml(blocker.message || blocker.code || 'Блокер')}${escapeHtml(suffix)}${reviewLink}</li>`;
        }).join('');
        panel.hidden = false;
        panel.innerHTML = `
            <strong>Генерация заблокирована</strong>
            <ul>${items || '<li>Проверьте readiness учебного плана.</li>'}</ul>
        `;
    }

    function renderFilteredProjects() {
        const projects = getFilteredProjects();
        renderProjects(projects);
        updateStatusSummary();
    }

    function getFilteredProjects() {
        const statusFilter = document.getElementById('learningProjectStatusFilter')?.value || 'all';
        const query = (document.getElementById('learningProjectSearch')?.value || '').trim().toLocaleLowerCase('ru-RU');
        return currentProjects.filter((project) => {
            if (statusFilter !== 'all' && projectFilterKey(project) !== statusFilter) {
                return false;
            }
            if (!query) {
                return true;
            }
            return projectSearchText(project).includes(query);
        });
    }

    function projectFilterKey(project) {
        const status = String(project.generation_status || 'not_started');
        if (!currentPlanReady && status === 'not_started') return 'blocked';
        if (project.can_generate && status === 'not_started') return 'available';
        if (ACTIVE_STATUSES.has(status) && status !== 'needs_review') return 'active';
        return status;
    }

    function projectSearchText(project) {
        const values = [
            project.title,
            project.platform_name,
            project.block_name,
            project.description,
            STATUS_LABELS[project.generation_status],
            ...(Array.isArray(project.skills) ? project.skills : []),
            ...(Array.isArray(project.learning_outcomes) ? project.learning_outcomes : []),
        ];
        return values.filter(Boolean).join(' ').toLocaleLowerCase('ru-RU');
    }

    function updateStatusSummary(planTitle) {
        const visibleCount = getFilteredProjects().length;
        const title = planTitle || document.getElementById('learningProjectsStatus')?.dataset.planTitle || '';
        const status = document.getElementById('learningProjectsStatus');
        if (!status) return;
        if (planTitle) status.dataset.planTitle = planTitle;
        const prefix = title ? `${title} · ` : '';
        status.textContent = `${prefix}${visibleCount} из ${currentProjects.length} проектов`;
        status.classList.remove('is-error');
    }

    function renderProjects(projects) {
        const body = document.getElementById('learningProjectsTableBody');
        if (!body) return;
        if (!projects.length) {
            const emptyText = currentProjects.length ? 'Под выбранные фильтры нет проектов.' : 'Проекты не найдены.';
            body.innerHTML = `<tr><td colspan="5" class="learning-projects-empty">${emptyText}</td></tr>`;
            return;
        }
        body.innerHTML = projects.map((project, index) => renderProjectRow(project, index)).join('');
    }

    function renderProjectRow(project, index) {
        const generation = project.generation || {};
        const rawStatus = String(project.generation_status || 'not_started');
        const blocked = !currentPlanReady && rawStatus === 'not_started';
        const status = blocked ? 'blocked' : rawStatus;
        const active = ACTIVE_STATUSES.has(status);
        const completed = status === 'completed';
        const failed = status === 'failed';
        const statusClass = completed ? 'is-completed' : failed ? 'is-failed' : blocked ? 'is-blocked' : active ? 'is-active' : '';
        const updatedAt = formatDate(generation.updated_at || generation.created_at);
        const title = project.title || project.platform_name || `Проект ${project.project_order || index + 1}`;
        const rowId = project.identity?.plan_row_id ? `row ${project.identity.plan_row_id}` : `#${project.project_order || index + 1}`;
        return `
            <tr>
                <td>
                    <div class="learning-project-title">
                        <strong>${escapeHtml(title)}</strong>
                        <span>${escapeHtml(project.platform_name || rowId)}</span>
                        ${renderGenerationHistory(project)}
                    </div>
                </td>
                <td>
                    <div class="learning-project-muted">${escapeHtml(project.block_name || '—')}</div>
                </td>
                <td>
                    <span class="learning-project-status ${statusClass}">${escapeHtml(STATUS_LABELS[status] || status)}</span>
                </td>
                <td>
                    <span class="learning-project-muted">${escapeHtml(updatedAt || '—')}</span>
                </td>
                <td>
                    ${renderProjectActions(project, index, status, active, completed, failed)}
                </td>
            </tr>
        `;
    }

    function renderProjectActions(project, index, status, active, completed, failed) {
        const generation = project.generation || {};
        const uiIndex = Number.isInteger(project._uiIndex) ? project._uiIndex : index;
        const actions = [];
        if (active && generation.request_id) {
            actions.push(`<button class="learning-project-action" type="button" data-action="open-run" data-index="${uiIndex}">Открыть</button>`);
        }
        if (project.can_generate) {
            const label = completed ? 'Новая генерация' : failed ? 'Повторить' : 'Сгенерировать';
            actions.push(`<button class="learning-project-action primary" type="button" data-action="generate" data-index="${uiIndex}">${label}</button>`);
        } else if (!active) {
            const disabledLabel = status === 'blocked' ? 'Блокер УП' : status === 'not_started' ? 'Недоступно' : 'Ожидает';
            actions.push(`<button class="learning-project-action" type="button" disabled>${disabledLabel}</button>`);
        }
        if (completed && generation.result_url) {
            actions.push(`<a class="learning-project-action" href="${escapeHtml(generation.result_url)}">Скачать</a>`);
        }
        return `<div class="learning-project-actions">${actions.join('')}</div>`;
    }

    function renderGenerationHistory(project) {
        const history = Array.isArray(project.generation_history) ? project.generation_history : [];
        const total = Number(project.generation_runs_count || history.length);
        if (!history.length) {
            return '<span class="learning-project-history-empty">Запусков нет</span>';
        }
        const rows = history.map((run) => {
            const status = String(run.status || 'not_started');
            const label = STATUS_LABELS[status] || status;
            const date = formatDate(run.updated_at || run.created_at);
            const score = run.score?.label || run.score?.total || '';
            const pipeline = run.pipeline_run_id ? ` · ${run.pipeline_run_id}` : '';
            return `
                <li>
                    <span>${escapeHtml(label)}</span>
                    <small>${escapeHtml([date, score].filter(Boolean).join(' · '))}${escapeHtml(pipeline)}</small>
                </li>
            `;
        }).join('');
        const suffix = total > history.length ? ` · последние ${history.length}` : '';
        return `
            <details class="learning-project-history">
                <summary>${total} запуск${total === 1 ? '' : 'ов'}${escapeHtml(suffix)}</summary>
                <ol>${rows}</ol>
            </details>
        `;
    }

    function formatDate(value) {
        if (!value) return '';
        const raw = String(value).trim();
        const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(raw);
        const date = new Date(hasTimezone ? raw : `${raw.replace(' ', 'T')}Z`);
        if (Number.isNaN(date.getTime())) return raw;
        return date.toLocaleString('ru-RU', {
            day: '2-digit',
            month: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
        });
    }

    function openProjectInGenerator(project) {
        const identity = project.identity || {};
        const params = new URLSearchParams({
            plan_id: String(identity.source_plan_id || currentPlanId),
            block: project.block_name || '',
            project_order: String(project.project_order || identity.project_order || identity.project_index || ''),
        });
        window.location.href = `/app/generate?${params.toString()}`;
    }

    function openExistingRun(project) {
        const generation = project.generation || {};
        if (!generation.request_id) return;
        const status = generation.status || project.generation_status || 'pending';
        sessionStorage.setItem('generation_state', JSON.stringify({
            requestId: generation.request_id,
            currentGenerationStatus: status,
            lastKnownGenerationPhase: generation.stage || 'generation',
            lastKnownGenerationProgress: status === 'needs_review' ? 70 : 1,
            lastKnownGenerationAgent: status === 'needs_review' ? 'Ожидание методолога' : 'Продолжение генерации',
            timestamp: Date.now(),
        }));
        const params = new URLSearchParams({
            request_id: generation.request_id,
            pipeline_run_id: generation.pipeline_run_id || '',
        });
        window.location.href = `/app/generate?${params.toString()}`;
    }

    async function refreshCurrentPlan() {
        const select = document.getElementById('learningProjectPlanSelect');
        const planId = select?.value || currentPlanId;
        if (planId) {
            await loadPlan(planId);
        } else {
            await loadPlans();
        }
    }

    function bindEvents() {
        document.getElementById('learningProjectPlanSelect')?.addEventListener('change', (event) => {
            const planId = event.target.value;
            if (planId) {
                loadPlan(planId).catch((error) => setStatus(error.message, true));
            }
        });
        document.getElementById('learningProjectsRefreshBtn')?.addEventListener('click', () => {
            refreshCurrentPlan().catch((error) => setStatus(error.message, true));
        });
        document.getElementById('learningProjectStatusFilter')?.addEventListener('change', renderFilteredProjects);
        document.getElementById('learningProjectSearch')?.addEventListener('input', renderFilteredProjects);
        document.getElementById('learningProjectsTableBody')?.addEventListener('click', (event) => {
            const button = event.target.closest('[data-action]');
            if (!button) return;
            const project = currentProjects[Number(button.dataset.index)];
            if (!project) return;
            if (button.dataset.action === 'generate') openProjectInGenerator(project);
            if (button.dataset.action === 'open-run') openExistingRun(project);
        });
    }

    async function initLearningProjects() {
        updateUserHeader();
        bindEvents();
        if (window.ContentGenAuth?.ensureAuthPresent && !window.ContentGenAuth.ensureAuthPresent()) {
            return;
        }
        try {
            await loadPlans();
        } catch (error) {
            setStatus(error.message || 'Не удалось загрузить учебные проекты', true);
            renderProjects([]);
            updateStats({});
        }
    }

    window.handleLearningProjectsLogout = function handleLearningProjectsLogout() {
        window.ContentGenAuth?.clearAuthState?.();
        window.location.replace('/');
    };

    document.addEventListener('DOMContentLoaded', initLearningProjects);
})();
