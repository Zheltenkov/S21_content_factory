(function () {
    let activeMetricFilter = 'all';

    function getRuntime() {
        return window.ContentGenGenerationRuntime || {};
    }

    function getState() {
        const runtime = getRuntime();
        return typeof runtime.getState === 'function' ? runtime.getState() : {};
    }

    function escapeHtml(value) {
        if (typeof window.escapeHtmlSafe === 'function') {
            return window.escapeHtmlSafe(value);
        }
        if (window.sanitize?.escapeHtml) {
            return window.sanitize.escapeHtml(String(value ?? ''));
        }
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function safeSetHtml(container, html) {
        if (window.sanitize?.safeSetHTML) {
            window.sanitize.safeSetHTML(container, html);
        } else {
            container.innerHTML = html;
        }
    }

    function currentMetricFilter() {
        return getState().currentFilter || window.currentMetricFilter || activeMetricFilter || 'all';
    }

    function formatCompactNumber(value) {
        const num = Number(value || 0);
        if (!Number.isFinite(num) || num <= 0) return '-';
        return new Intl.NumberFormat('ru-RU').format(Math.round(num));
    }

    function countMarkdownWords(markdown) {
        if (typeof markdown !== 'string' || !markdown.trim()) return 0;
        return markdown
            .replace(/```[\s\S]*?```/g, ' ')
            .replace(/`[^`]*`/g, ' ')
            .replace(/[#[\]()*_>|-]/g, ' ')
            .split(/\s+/)
            .filter(Boolean).length;
    }

    function getRubricSummary(rubric) {
        const items = Array.isArray(rubric?.items) ? rubric.items : [];
        const max = Number(rubric?.max_score || (items.length || 0));
        const total = Number(rubric?.total || items.reduce((sum, item) => sum + Number(item?.score || 0), 0));
        const failed = items.filter((item) => Number(item?.score || 0) !== 1).length;
        const passed = items.length - failed;
        const percent = max > 0 ? Math.round((total / max) * 100) : 0;
        return { total, max, percent, passed, failed, count: items.length };
    }

    function setText(id, value) {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    }

    function setScoreRing(id, percent) {
        const ring = document.getElementById(id);
        if (ring) {
            ring.style.setProperty('--score', String(Math.max(0, Math.min(100, Number(percent) || 0))));
        }
    }

    function countResultAssets(result) {
        const files = result?.assets?.files;
        if (Array.isArray(files)) return files.length;
        if (files && typeof files === 'object') return Object.keys(files).length;
        const generated = result?.generated_files || result?.files;
        if (Array.isArray(generated)) return generated.length;
        if (generated && typeof generated === 'object') return Object.keys(generated).length;
        return 0;
    }

    function updateGenerationResultSummary(data) {
        const result = data?.result || {};
        const rubric = result.rubric || result.report_json?.rubric || null;
        const summary = getRubricSummary(rubric);
        const markdown = typeof result.markdown === 'string' ? result.markdown : '';
        const stats = result.text_stats || result.report_json?.text_stats || {};
        const words = Number(stats.words || stats.word_count || countMarkdownWords(markdown));
        const tasks = Number(result.task_plan?.tasks_count || result.practice_plan?.tasks_count || 0);
        const assets = countResultAssets(result);
        const scoreText = summary.max > 0 ? `${summary.total} / ${summary.max}` : '-';
        const scoreMeta = summary.max > 0
            ? (summary.percent >= 70 ? 'Порог качества пройден' : 'Ниже порога 70%')
            : 'Метрики появятся после проверки';

        setText('generationScoreValue', scoreText);
        setText('generationScorePercent', summary.max > 0 ? `${summary.percent}%` : '-');
        setText('generationScoreMeta', scoreMeta);
        setScoreRing('generationScoreRing', summary.percent);
        setText('generationWordsValue', formatCompactNumber(words));
        setText('generationTasksValue', tasks > 0 ? `${tasks}${result.task_plan?.bonus ? ' + 1' : ''}` : '-');
        setText('generationTasksMeta', tasks > 0 ? 'Практические задания' : 'План практики не найден');
        setText('generationAssetsValue', assets > 0 ? String(assets) : '-');
    }

    function updateCheckerScorePanel(rubric) {
        const panel = document.getElementById('checkerScorePanel');
        if (!panel) return;
        const summary = getRubricSummary(rubric);
        const scoreText = summary.max > 0 ? `${summary.total} / ${summary.max}` : '-';
        const isCheckerPage = document.body.classList.contains('page-checker');
        const statusText = summary.max > 0
            ? (summary.percent >= 70 ? 'Порог 70% пройден. README можно принимать.' : 'Ниже порога 70%. Рекомендуется улучшить README.')
            : 'Нет данных критериев';

        setText('checkerScoreValue', isCheckerPage && summary.max > 0 ? `${summary.percent} %` : scoreText);
        setText('checkerScorePercent', summary.max > 0 ? `${summary.percent}%` : '-');
        setText('checkerScoreStatus', isCheckerPage && summary.max > 0
            ? `Пройдено ${summary.total} из ${summary.max} критериев. ${summary.percent >= 70 ? 'Документ выше порога качества.' : 'Рекомендуется улучшить документ - система может извлечь данные и сгенерировать улучшенную версию.'}`
            : statusText);
        setScoreRing('checkerScoreRing', summary.percent);

        if (!isCheckerPage) return;
        const brandBadge = document.getElementById('checkerBrandBadge');
        const brandMark = document.getElementById('checkerBrandMark');
        const brandSub = document.getElementById('checkerBrandSub');
        const subbarStatus = document.getElementById('checkerSubbarStatus');
        const thresholdBadge = document.getElementById('checkerThresholdBadge');
        const ok = summary.percent >= 70;
        if (brandBadge) brandBadge.setAttribute('data-step', '04.1');
        if (brandMark) brandMark.textContent = 'ПРОВЕРКА';
        if (brandSub) brandSub.textContent = `${summary.percent} % · ${ok ? 'ПОРОГ ПРОЙДЕН' : 'НИЖЕ ПОРОГА'}`;
        if (subbarStatus) {
            subbarStatus.style.display = 'inline-flex';
            subbarStatus.className = `badge ${ok ? 'success' : 'warn'}`;
            subbarStatus.textContent = ok ? `✓ ${summary.total} из ${summary.max}` : `⚠ ${summary.percent}%`;
        }
        if (thresholdBadge) {
            thresholdBadge.textContent = ok ? 'ПОРОГ 70 % ПРОЙДЕН' : '⚠ НИЖЕ ПОРОГА 70 %';
            thresholdBadge.classList.toggle('warn', !ok);
            thresholdBadge.classList.toggle('success', ok);
        }
        const chips = document.getElementById('checkerScoreChips');
        if (chips) {
            chips.innerHTML = `
                <span class="chip">Все<span class="count">${summary.count}</span></span>
                <span class="chip ${summary.failed > 0 ? 'on' : ''}">Не пройдено<span class="count">${summary.failed}</span></span>
                <span class="chip">Пройдено<span class="count">${summary.passed}</span></span>
            `;
        }
    }

    function normalizeMetricStatus(item) {
        const rawStatus = String(item?.status || item?.result || '').toLowerCase();
        const score = Number(item?.score ?? item?.value ?? 0);
        const comments = Array.isArray(item?.comments) ? item.comments.join(' ') : String(item?.comments || item?.comment || '');
        const hasWarning = /warn|предуп|warning/i.test(rawStatus) || /предуп|warning/i.test(comments);
        if (hasWarning && score !== 0) return 'warning';
        if (rawStatus.includes('pass') || rawStatus.includes('пройден')) return 'passed';
        if (rawStatus.includes('fail') || rawStatus.includes('не пройден')) return 'failed';
        if (score >= 1) return 'passed';
        if (score > 0) return 'warning';
        return 'failed';
    }

    function normalizeMetricComment(item, status) {
        const comments = Array.isArray(item?.comments)
            ? item.comments.filter(Boolean)
            : [item?.comments || item?.comment || item?.message || ''].filter(Boolean);
        if (comments.length) return comments.join(' ');
        if (status === 'passed') return item?.description || 'Критерий выполнен.';
        if (status === 'warning') return item?.description || 'Нужна ручная проверка формулировки.';
        return item?.description || 'Критерий требует доработки.';
    }

    function getMetricGroupLabel(item) {
        const category = item?.section || item?.category || item?.group || '';
        if (category) return String(category);
        const id = String(item?.id || '').toUpperCase();
        if (id.startsWith('S')) return 'Структура';
        if (id.startsWith('R')) return 'Требования';
        if (id.startsWith('T')) return 'Сторителлинг и тон';
        if (id.startsWith('P')) return 'Практика';
        if (id.startsWith('D')) return 'Данные и артефакты';
        if (id.startsWith('Q')) return 'Качество';
        return 'Общие критерии';
    }

    function normalizeMetricItems(rubric) {
        const rawItems = Array.isArray(rubric?.items) ? rubric.items : [];
        return rawItems.map((item, index) => {
            const status = normalizeMetricStatus(item);
            return {
                id: String(item?.id || item?.code || index + 1),
                title: String(item?.title || item?.name || `Критерий ${index + 1}`),
                description: String(item?.description || ''),
                comment: normalizeMetricComment(item, status),
                status,
                group: getMetricGroupLabel(item)
            };
        });
    }

    function metricStatusLabel(status) {
        if (status === 'passed') return 'ПРОЙДЕН';
        if (status === 'warning') return 'ПРЕДУПР.';
        return 'НЕ ПРОЙДЕН';
    }

    function metricStatusIcon(status) {
        if (status === 'passed') return '✓';
        if (status === 'warning') return '⚠';
        return '×';
    }

    function renderS21MetricsView(rubric, container, containerId = '') {
        if (!container) return false;
        const items = normalizeMetricItems(rubric);
        const activeFilter = currentMetricFilter();
        const passed = items.filter((item) => item.status === 'passed').length;
        const failed = items.filter((item) => item.status === 'failed').length;
        const warnings = items.filter((item) => item.status === 'warning').length;
        const visibleItems = items.filter((item) => {
            if (activeFilter === 'passed') return item.status === 'passed';
            if (activeFilter === 'failed') return item.status === 'failed';
            if (activeFilter === 'warning') return item.status === 'warning';
            return true;
        });

        const grouped = visibleItems.reduce((acc, item) => {
            if (!acc[item.group]) acc[item.group] = [];
            acc[item.group].push(item);
            return acc;
        }, {});
        const summary = getRubricSummary(rubric);
        const totalCount = items.length || summary.count || 0;
        const scoreText = summary.max > 0 ? `${summary.total} из ${summary.max}` : `${passed} из ${totalCount}`;

        const filterButton = (filter, label, count) => `
            <button type="button" class="s21-metric-filter metrics-filter-btn ${activeFilter === filter ? 'active' : ''}" data-filter="${filter}">
                ${label}<span>${count}</span>
            </button>
        `;
        const groupsHtml = Object.entries(grouped).map(([group, groupItems]) => {
            const rows = groupItems.map((item) => `
                <div class="s21-metric-row ${item.status}">
                    <div class="s21-metric-icon" aria-hidden="true">${metricStatusIcon(item.status)}</div>
                    <div class="s21-metric-code">${escapeHtml(item.id)}</div>
                    <div class="s21-metric-main">
                        <strong>${escapeHtml(item.title)}</strong>
                        <p>${escapeHtml(item.comment || item.description)}</p>
                    </div>
                    <span class="s21-metric-status ${item.status}">${metricStatusLabel(item.status)}</span>
                </div>
            `).join('');
            return `
                <section class="s21-metric-group-card">
                    <div class="s21-metric-group-head"><strong>${escapeHtml(group)}</strong><span>${groupItems.length} критериев</span></div>
                    ${rows}
                </section>
            `;
        }).join('');

        safeSetHtml(container, `
            <div class="s21-metrics-view" data-container="${escapeHtml(containerId)}">
                <div class="s21-metric-toolbar">
                    <div class="s21-metric-filters">
                        ${filterButton('all', 'Все', totalCount)}
                        ${filterButton('passed', 'Пройдены', passed)}
                        ${filterButton('failed', 'Не пройдены', failed)}
                        ${filterButton('warning', 'Предупреждения', warnings)}
                    </div>
                    <div class="s21-metric-grouping">
                        <span>Группировка:</span>
                        <strong>по разделу</strong>
                        <small>${escapeHtml(scoreText)}</small>
                    </div>
                </div>
                ${groupsHtml || '<div class="s21-metric-empty">Нет критериев для выбранного фильтра.</div>'}
            </div>
        `);
        return true;
    }

    function renderCheckerMetricsReview(rubric, container, containerId = '') {
        if (!container) return false;
        const items = normalizeMetricItems(rubric);
        const activeFilter = currentMetricFilter();
        const counts = {
            all: items.length,
            passed: items.filter((item) => item.status === 'passed').length,
            failed: items.filter((item) => item.status === 'failed').length,
            warning: items.filter((item) => item.status === 'warning').length
        };
        const visibleItems = items.filter((item) => {
            if (activeFilter === 'passed') return item.status === 'passed';
            if (activeFilter === 'failed') return item.status === 'failed';
            if (activeFilter === 'warning') return item.status === 'warning';
            return true;
        });
        const summary = getRubricSummary(rubric);
        const priorityFor = (item) => {
            if (item.status === 'failed') return 'Высокий';
            if (item.status === 'warning') return 'Средний';
            return 'Низкий';
        };
        const filterButton = (filter, label, count) => `
            <button type="button" class="checker-filter-pill metrics-filter-btn ${activeFilter === filter ? 'active' : ''}" data-filter="${filter}">
                ${label}<span>${count}</span>
            </button>
        `;
        const rows = visibleItems.map((item) => {
            const priority = priorityFor(item);
            return `
                <div class="checker-criterion-row ${item.status}">
                    <div class="checker-criterion-icon">${metricStatusIcon(item.status)}</div>
                    <div class="checker-criterion-code">${escapeHtml(item.id)}</div>
                    <div class="checker-criterion-main">
                        <strong>${escapeHtml(item.title)}</strong>
                        <p>${escapeHtml(item.comment || item.description)}</p>
                    </div>
                    <span class="checker-criterion-tag">${escapeHtml(item.group).toUpperCase()}</span>
                    <span class="checker-criterion-priority ${priority === 'Высокий' ? 'high' : priority === 'Средний' ? 'medium' : 'low'}">${priority.toUpperCase()}</span>
                </div>
            `;
        }).join('');

        safeSetHtml(container, `
            <div class="checker-metrics-view" data-container="${escapeHtml(containerId)}">
                <div class="checker-metrics-toolbar">
                    <div class="checker-filter-row">
                        ${filterButton('all', 'Все', counts.all)}
                        ${filterButton('failed', 'Не пройдено', counts.failed)}
                        ${filterButton('warning', 'Предупреждения', counts.warning)}
                        ${filterButton('passed', 'Пройдено', counts.passed)}
                    </div>
                    <div class="checker-filter-row secondary">
                        <button type="button" class="checker-filter-pill muted">Раздел: Структура</button>
                        <button type="button" class="checker-filter-pill muted">Приоритет: Высокий</button>
                    </div>
                </div>
                <div class="checker-criteria-list">
                    ${rows || '<div class="s21-metric-empty">Нет критериев для выбранного фильтра.</div>'}
                </div>
                <div class="checker-metrics-summary">${summary.max > 0 ? `Пройдено ${summary.total} из ${summary.max} критериев.` : ''}</div>
            </div>
        `);
        return true;
    }

    function displayMetrics(rubric, containerId = null) {
        const state = getState();
        const activeVersion = state.currentMetricsVersion || window.currentMetricsVersion || 'original';
        const resolvedContainerId = containerId || (activeVersion === 'original' ? 'metricsContentOriginal' : 'metricsContentRegen');
        const container = document.getElementById(resolvedContainerId);
        if (!container) {
            console.error('Контейнер не найден для displayMetrics:', resolvedContainerId);
            return;
        }

        const checkerContainer = document.body.classList.contains('page-checker')
            && ['checkerMetricsOriginal', 'checkerMetricsImproved', 'checkerMetrics'].includes(resolvedContainerId);
        if (checkerContainer) {
            renderCheckerMetricsReview(rubric, container, resolvedContainerId);
            updateCheckerScorePanel(rubric);
            return;
        }

        renderS21MetricsView(rubric, container, resolvedContainerId);
        if (resolvedContainerId === 'checkerMetricsOriginal' || resolvedContainerId === 'checkerMetricsImproved') {
            updateCheckerScorePanel(rubric);
        }
    }

    function toggleDescriptionColumn() {
        const checkbox = document.getElementById('toggleDescription');
        const columns = document.querySelectorAll('.description-column');
        if (!checkbox || columns.length === 0) return;
        const isVisible = checkbox.checked;
        columns.forEach((col) => {
            col.style.display = isVisible ? '' : 'none';
        });
    }

    function displayReport(result, containerId = null) {
        const state = getState();
        const activeVersion = state.currentReportVersion || window.currentReportVersion || 'original';
        const resolvedContainerId = containerId || (activeVersion === 'original' ? 'reportContentOriginal' : 'reportContentRegen');
        const container = document.getElementById(resolvedContainerId);
        if (!container) return;

        const stats = result?.text_stats || result?.report_json?.text_stats || {};
        let html = '<h3>Статистика по тексту</h3><div class="metrics-grid">';
        html += `<div class="metric-card"><div class="metric-value">${Number(stats.chars_total || stats.chars || 0).toLocaleString()}</div><div class="metric-label">Символов</div></div>`;
        html += `<div class="metric-card"><div class="metric-value">${Number(stats.words || 0).toLocaleString()}</div><div class="metric-label">Слов</div></div>`;
        html += `<div class="metric-card"><div class="metric-value">${Number(stats.sentences || 0).toLocaleString()}</div><div class="metric-label">Предложений</div></div>`;
        html += `<div class="metric-card"><div class="metric-value">${Number(stats.lines || 0).toLocaleString()}</div><div class="metric-label">Строк</div></div>`;
        html += `<div class="metric-card"><div class="metric-value">${Number(stats.tokens || 0).toLocaleString()}</div><div class="metric-label">Токенов</div></div>`;
        html += '</div>';
        if (stats.readability_index) {
            html += `<div class="metric-card s21-metric-extra"><div class="metric-value">${Number(stats.readability_index).toFixed(1)}</div><div class="metric-label">Индекс читаемости</div></div>`;
        }
        safeSetHtml(container, html);
    }

    function filterMetrics(filter) {
        if (!filter) {
            console.error('Фильтр не указан');
            return;
        }
        activeMetricFilter = filter;
        window.currentMetricFilter = filter;
        window.ContentGenStores?.resultStore?.setState?.({ currentFilter: filter });
        const state = getState();
        const metricsVersion = state.currentMetricsVersion || window.currentMetricsVersion || 'original';
        let rubric = null;
        let containerId = null;

        const checkerOriginalContainer = document.getElementById('checkerMetricsOriginal');
        const checkerImprovedContainer = document.getElementById('checkerMetricsImproved');
        const checkerContainer = document.getElementById('checkerMetrics');
        if (checkerOriginalContainer && checkerOriginalContainer.style.display !== 'none') {
            rubric = window.checkerRubric || {};
            containerId = 'checkerMetricsOriginal';
        } else if (checkerImprovedContainer && checkerImprovedContainer.style.display !== 'none') {
            rubric = window.improvedRubric || {};
            containerId = 'checkerMetricsImproved';
        } else if (checkerContainer && checkerContainer.parentElement && checkerContainer.parentElement.style.display !== 'none') {
            rubric = window.checkerRubric || {};
            containerId = 'checkerMetrics';
        } else if (metricsVersion === 'original' && state.originalRubric) {
            rubric = state.originalRubric;
            containerId = 'metricsContentOriginal';
        } else if (metricsVersion === 'regenerated' && state.regeneratedRubric) {
            rubric = state.regeneratedRubric;
            containerId = 'metricsContentRegen';
        } else {
            rubric = window.currentRubric || {};
        }

        if (!rubric || !Array.isArray(rubric.items)) {
            console.error('Нет данных для фильтрации', { rubric, metricsVersion, containerId });
            return;
        }
        displayMetrics(rubric, containerId || (metricsVersion === 'original' ? 'metricsContentOriginal' : 'metricsContentRegen'));
    }

    Object.assign(window, {
        displayMetrics,
        displayReport,
        filterMetrics,
        getRubricSummary,
        updateGenerationResultSummary,
        updateCheckerScorePanel,
        toggleDescriptionColumn
    });
})();
