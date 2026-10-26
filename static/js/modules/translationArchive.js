/**
 * Перевод архива проекта: опись с галочками, смета, прогон, скачивание.
 *
 * Отдельный модуль, а не ветка в translationPage.js, потому что это другой поток: там один
 * файл и один запрос, здесь два шага и таблица на сотню строк. Общего между ними — только
 * опрос статуса, и он здесь свой, потому что показывает имя текущего файла, а не фазу.
 *
 * Вся логика страницы живёт здесь, а не во встроенном <script>: корпоративный прокси школы
 * обрывает ответ, внутри которого есть встроенный скрипт с загрузкой файлов.
 */
(function () {
    'use strict';

    const POLL_INTERVAL_MS = 2000;

    let plan = null;          // ответ inspect: plan_id, entries, totals
    let pollTimer = null;
    let currentRequestId = null;

    function apiUrl() {
        return typeof window.getApiUrl === 'function' ? window.getApiUrl() : '/api/v1';
    }

    function authHeaders() {
        return typeof window.getAuthHeaders === 'function' ? window.getAuthHeaders() : {};
    }

    function byId(id) {
        return document.getElementById(id);
    }

    function setStatus(html) {
        const slot = byId('translationStatus');
        if (slot) slot.innerHTML = html;
    }

    function setError(message) {
        const slot = byId('translationStatus');
        if (!slot) return;
        if (window.sanitize) {
            window.sanitize.safeSetErrorMessage(slot, message);
        } else {
            // textContent экранирует сам — сообщение приходит с сервера.
            slot.textContent = message;
        }
        if (window.toast) window.toast.error(message);
    }

    /** Человеческое имя для причины пропуска и для языка. */
    function languageLabel(code) {
        if (code === 'ru') return 'русский';
        if (code === 'other') return 'не русский';
        return '—';
    }

    function formatMoney(usd) {
        return '$' + Number(usd || 0).toFixed(2);
    }

    function formatDuration(seconds) {
        const total = Math.max(0, Math.round(Number(seconds) || 0));
        if (total < 60) return total + ' с';
        const minutes = Math.floor(total / 60);
        return minutes + ' мин' + (total % 60 ? ' ' + (total % 60) + ' с' : '');
    }

    // ------------------------------------------------------------------ шаг 1: опись

    function selectedArchiveFile() {
        const input = byId('translationArchiveFile');
        return (input && input.files && input.files[0]) || null;
    }

    function handleArchiveFileSelect() {
        const file = selectedArchiveFile();
        const name = byId('translationArchiveFileName');
        if (name) name.textContent = file ? file.name : 'Архив проекта (.zip)';
        resetArchivePlan();
        if (file) inspectArchive();
    }

    function clearArchiveFile() {
        const input = byId('translationArchiveFile');
        if (input) input.value = '';
        const name = byId('translationArchiveFileName');
        if (name) name.textContent = 'Архив проекта (.zip)';
        resetArchivePlan();
    }

    function resetArchivePlan() {
        plan = null;
        stopPolling();
        const panel = byId('translationArchivePlanPanel');
        if (panel) panel.style.display = 'none';
        const result = byId('translationArchiveResult');
        if (result) result.style.display = 'none';
        setStatus('');
    }

    async function inspectArchive() {
        const file = selectedArchiveFile();
        if (!file) {
            setError('Выберите архив .zip с файлами проекта');
            return;
        }
        setStatus('<div class="info-box">Разбираем архив. Модель на этом шаге не вызывается.</div>');

        const form = new FormData();
        form.append('file', file);
        try {
            const response = await fetch(apiUrl() + '/translate/archive/inspect', {
                method: 'POST',
                headers: authHeaders(),
                body: form,
            });
            const payload = await response.json().catch(function () { return {}; });
            if (!response.ok) {
                setError(payload.detail || ('Не удалось разобрать архив: ' + response.status));
                return;
            }
            plan = payload;
            renderPlan();
            setStatus('<div class="info-box">Отметьте файлы и запустите перевод. Смета пересчитывается по галочкам.</div>');
        } catch (error) {
            setError('Не удалось разобрать архив: ' + error.message);
        }
    }

    function renderPlan() {
        const panel = byId('translationArchivePlanPanel');
        const body = byId('translationArchiveRows');
        if (!panel || !body || !plan) return;

        body.textContent = '';
        plan.entries.forEach(function (entry, index) {
            body.appendChild(renderRow(entry, index));
        });
        panel.style.display = 'block';
        updateTotals();
    }

    function renderRow(entry, index) {
        const row = document.createElement('tr');
        if (!entry.handler) row.classList.add('is-untranslatable');

        const tick = document.createElement('td');
        const box = document.createElement('input');
        box.type = 'checkbox';
        box.checked = !!entry.selected;
        box.disabled = !entry.handler || entry.units === 0;
        box.dataset.path = entry.path;
        box.dataset.chars = String(entry.chars);
        box.dataset.index = String(index);
        box.addEventListener('change', updateTotals);
        tick.appendChild(box);
        row.appendChild(tick);

        row.appendChild(cell(entry.path, 'translate-archive-path'));
        row.appendChild(cell(entry.handler ? entry.handler.toUpperCase() : '—'));
        row.appendChild(cell(entry.units ? String(entry.units) : '—'));
        row.appendChild(cell(entry.chars ? String(entry.chars) : '—'));
        row.appendChild(cell(languageLabel(entry.language)));
        row.appendChild(cell(entry.skip_reason || ''));
        return row;
    }

    function cell(text, className) {
        const td = document.createElement('td');
        td.textContent = text;
        if (className) td.className = className;
        return td;
    }

    function tickedBoxes() {
        return Array.prototype.slice.call(
            document.querySelectorAll('#translationArchiveRows input[type="checkbox"]')
        ).filter(function (box) { return box.checked && !box.disabled; });
    }

    /**
     * Пересчитывает смету по галочкам на клиенте.
     *
     * Цена за знак берётся из сметы сервера — делением, а не вторым запросом: ставка одна и
     * та же, а запрос на каждую галочку был бы расточительством. Если сервер посчитал ноль
     * (нечего переводить), ставка тоже ноль, и делить незачем.
     */
    function updateTotals() {
        if (!plan) return;
        const boxes = tickedBoxes();
        const chars = boxes.reduce(function (sum, box) { return sum + Number(box.dataset.chars || 0); }, 0);
        const baseChars = Number(plan.totals.chars || 0);
        const share = baseChars > 0 ? chars / baseChars : 0;

        setText('translationArchiveTotalFiles', String(boxes.length));
        setText('translationArchiveTotalChars', String(chars));
        setText('translationArchiveTotalTime', formatDuration(Number(plan.totals.seconds || 0) * share));
        setText('translationArchiveTotalMoney', formatMoney(Number(plan.totals.usd || 0) * share));
        setText('translationArchiveModel', plan.totals.model || '—');

        const button = byId('translationArchiveStartBtn');
        if (button) button.disabled = boxes.length === 0;
    }

    function setText(id, value) {
        const node = byId(id);
        if (node) node.textContent = value;
    }

    function tickAll(state) {
        Array.prototype.slice.call(
            document.querySelectorAll('#translationArchiveRows input[type="checkbox"]')
        ).forEach(function (box) {
            if (!box.disabled) box.checked = state;
        });
        updateTotals();
    }

    function tickRussianOnly() {
        if (!plan) return;
        Array.prototype.slice.call(
            document.querySelectorAll('#translationArchiveRows input[type="checkbox"]')
        ).forEach(function (box) {
            if (box.disabled) return;
            const entry = plan.entries[Number(box.dataset.index)];
            box.checked = !!entry && entry.language === 'ru';
        });
        updateTotals();
    }

    // ------------------------------------------------------------------ шаг 2: прогон

    async function startArchiveTranslation() {
        if (!plan) {
            setError('Сначала разберите архив');
            return;
        }
        const paths = tickedBoxes().map(function (box) { return box.dataset.path; });
        if (!paths.length) {
            setError('Не отмечен ни один файл');
            return;
        }
        // Выбор языка у архива свой: общий живёт в экране документа, а тот в этот момент скрыт.
        const language = byId('translationArchiveLanguage') || byId('translationLanguage');
        const form = new FormData();
        form.append('target_language', language ? language.value : 'en');
        form.append('paths', JSON.stringify(paths));

        setStatus('<div class="info-box">Перевод запущен. Файлы переводятся по одному.</div>');
        showProgress('Запуск', 0);
        try {
            const response = await fetch(
                apiUrl() + '/translate/archive/' + encodeURIComponent(plan.plan_id) + '/start',
                { method: 'POST', headers: authHeaders(), body: form }
            );
            const payload = await response.json().catch(function () { return {}; });
            if (!response.ok) {
                hideProgress();
                setError(payload.detail || ('Не удалось запустить перевод: ' + response.status));
                return;
            }
            currentRequestId = payload.request_id;
            // План на сервере одноразовый: после прогона он забыт, и повторный старт не пройдёт.
            const button = byId('translationArchiveStartBtn');
            if (button) button.disabled = true;
            pollTimer = setInterval(pollOnce, POLL_INTERVAL_MS);
        } catch (error) {
            hideProgress();
            setError('Не удалось запустить перевод: ' + error.message);
        }
    }

    async function pollOnce() {
        if (!currentRequestId) return;
        try {
            const response = await fetch(apiUrl() + '/translate/status/' + currentRequestId, {
                headers: authHeaders(),
            });
            if (!response.ok) {
                stopPolling();
                hideProgress();
                setError(response.status === 404
                    ? 'Задача перевода не найдена. Запустите перевод заново.'
                    : 'Не удалось получить статус: ' + response.status);
                return;
            }
            const job = await response.json();
            showProgress(job.phase || 'перевод', Math.round((job.progress || 0) * 100));

            if (job.status === 'completed') {
                stopPolling();
                hideProgress();
                showResult(job);
            } else if (job.status === 'failed') {
                stopPolling();
                hideProgress();
                setError(job.error || 'Перевод архива не удался');
            }
        } catch (error) {
            stopPolling();
            hideProgress();
            setError('Не удалось получить статус: ' + error.message);
        }
    }

    function stopPolling() {
        if (pollTimer) {
            clearInterval(pollTimer);
            pollTimer = null;
        }
    }

    function showProgress(phase, percent) {
        const container = byId('translationProgressContainer');
        const phaseEl = byId('translationProgressPhase');
        const bar = byId('translationProgressBar');
        if (container) container.style.setProperty('display', 'block', 'important');
        if (phaseEl) phaseEl.textContent = phase;
        if (bar) bar.style.width = Math.max(0, Math.min(100, percent)) + '%';
    }

    function hideProgress() {
        const container = byId('translationProgressContainer');
        if (container) container.style.setProperty('display', 'none', 'important');
    }

    function showResult(job) {
        const panel = byId('translationArchiveResult');
        if (!panel) return;
        panel.style.display = 'block';
        setStatus('<div class="success-box">Архив переведён. Отчёт лежит внутри — ПЕРЕВОД-ОТЧЁТ.md.</div>');

        const report = byId('translationArchiveReport');
        if (report) report.textContent = job.translated_markdown || '';
        const button = byId('translationArchiveDownloadBtn');
        if (button) {
            button.onclick = function () {
                if (typeof window.downloadTranslationArtifact === 'function') {
                    window.downloadTranslationArtifact(currentRequestId, 'zip');
                }
            };
        }
    }

    if (typeof window !== 'undefined') {
        Object.assign(window, {
            handleArchiveFileSelect,
            clearArchiveFile,
            inspectArchive,
            startArchiveTranslation,
            archiveTickAll: tickAll,
            archiveTickRussianOnly: tickRussianOnly,
            resetArchivePlan,
        });
    }
})();
