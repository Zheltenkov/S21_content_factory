// Generator result tabs and README/data preview controller.
// Keeps result-specific rendering separate from generation orchestration.

        const resultStore = window.ContentGenStores?.resultStore || null;
        const regenerationStore = window.ContentGenStores?.regenerationStore || null;

        function getResultStoreState() {
            return resultStore?.getState?.() || {};
        }

        function setResultStoreState(updates = {}) {
            resultStore?.setState?.(updates);
        }

        function getRegenerationStoreState() {
            return regenerationStore?.getState?.() || {};
        }

        function setRegenerationStoreState(updates = {}) {
            regenerationStore?.setState?.(updates);
        }

        function getResultReadmeRenderMode() {
            return getResultStoreState().readmeRenderMode || 'preview';
        }

        function isResultReadmeComparisonActive() {
            return Boolean(getResultStoreState().readmeComparisonActive);
        }

        function getRegenerationSections() {
            return getRegenerationStoreState().sections || [];
        }

        function getRegenerationInstructionHistory() {
            return getRegenerationStoreState().instructionHistory || [];
        }

        function getRegenerationSectionDrafts() {
            return getRegenerationStoreState().sectionDrafts || {};
        }

        function getResultRuntimeState() {
            const runtime = window.ContentGenGenerationRuntime || {};
            return typeof runtime.getState === 'function' ? runtime.getState() : {};
        }

        function getActiveResultTabName() {
            return document.querySelector('.generation-final-card .tab-content.active')?.id || 'readme';
        }

        function getRegeneratedMarkdownFromState(state = getResultRuntimeState()) {
            const result = state.currentResult || window.currentResult || {};
            const resultState = getResultStoreState();
            return String(
                resultState.regeneratedMarkdown
                || state.regeneratedMarkdown
                || result.regenerated_markdown
                || result.regenerated_md
                || result.regenerated?.regenerated_md
                || result.report_json?.regenerated_markdown
                || result.report_json?.regenerated_md
                || ''
            );
        }

        function getOriginalMarkdownForComparison(state = getResultRuntimeState()) {
            const result = state.currentResult || window.currentResult || {};
            return String(
                state.originalMarkdown
                || result.original_markdown
                || result.report_json?.original_markdown
                || result.markdown
                || result.report_json?.markdown
                || ''
            );
        }

        function updateReadmeModeButtons() {
            document.getElementById('readmeModeMarkdown')?.classList.toggle(
                'active',
                !isResultReadmeComparisonActive() && getResultReadmeRenderMode() === 'markdown'
            );
            document.getElementById('readmeModePreview')?.classList.toggle(
                'active',
                !isResultReadmeComparisonActive() && getResultReadmeRenderMode() === 'preview'
            );
            document.getElementById('readmeModeCompare')?.classList.toggle('active', isResultReadmeComparisonActive());
        }

        function renderMarkdownByMode(containerId, markdown) {
            const container = document.getElementById(containerId);
            if (!container) return;
            if (getResultReadmeRenderMode() === 'preview') {
                displayMarkdown(markdown, containerId);
                return;
            }
            const escaped = escapeHtmlSafe(markdown || '');
            container.innerHTML = `<pre class="result-markdown-source">${escaped}</pre>`;
        }

        function buildRegenerationLineDiff(originalMarkdown, regeneratedMarkdown) {
            const originalLines = String(originalMarkdown || '').split(/\r?\n/);
            const regeneratedLines = String(regeneratedMarkdown || '').split(/\r?\n/);
            const dp = Array.from(
                { length: originalLines.length + 1 },
                () => Array(regeneratedLines.length + 1).fill(0)
            );

            for (let i = originalLines.length - 1; i >= 0; i -= 1) {
                for (let j = regeneratedLines.length - 1; j >= 0; j -= 1) {
                    dp[i][j] = originalLines[i] === regeneratedLines[j]
                        ? dp[i + 1][j + 1] + 1
                        : Math.max(dp[i + 1][j], dp[i][j + 1]);
                }
            }

            const lines = [];
            let originalIndex = 0;
            let regeneratedIndex = 0;

            while (originalIndex < originalLines.length || regeneratedIndex < regeneratedLines.length) {
                const original = originalLines[originalIndex];
                const regenerated = regeneratedLines[regeneratedIndex];
                if (
                    originalIndex < originalLines.length
                    && regeneratedIndex < regeneratedLines.length
                    && original === regenerated
                ) {
                    lines.push({
                        type: 'equal',
                        text: original,
                        originalLine: originalIndex + 1,
                        regeneratedLine: regeneratedIndex + 1,
                    });
                    originalIndex += 1;
                    regeneratedIndex += 1;
                    continue;
                }
                if (
                    originalIndex < originalLines.length
                    && (
                        regeneratedIndex >= regeneratedLines.length
                        || dp[originalIndex + 1][regeneratedIndex] >= dp[originalIndex][regeneratedIndex + 1]
                    )
                ) {
                    lines.push({
                        type: 'delete',
                        text: original,
                        originalLine: originalIndex + 1,
                        regeneratedLine: null,
                    });
                    originalIndex += 1;
                    continue;
                }
                if (regeneratedIndex < regeneratedLines.length) {
                    lines.push({
                        type: 'insert',
                        text: regenerated,
                        originalLine: null,
                        regeneratedLine: regeneratedIndex + 1,
                    });
                    regeneratedIndex += 1;
                }
            }

            return {
                lines,
                originalLineCount: originalLines.length,
                regeneratedLineCount: regeneratedLines.length,
                deletedLineCount: lines.filter(line => line.type === 'delete').length,
                insertedLineCount: lines.filter(line => line.type === 'insert').length,
            };
        }

        function renderRegenerationDiffLine(line) {
            const heading = line.type === 'delete' ? null : extractMarkdownHeadingTitle(line.text);
            const headingAttr = heading ? ` data-heading="${escapeResultHtml(heading.title)}"` : '';
            const headingClass = heading ? ` is-heading level-${heading.level}` : '';
            const lineNumber = line.type === 'insert' ? line.regeneratedLine : line.originalLine;
            const marker = line.type === 'insert' ? '+' : (line.type === 'delete' ? '−' : '');
            const text = heading ? heading.title : String(line.text || '');
            return `
                <div class="regeneration-diff-line is-${line.type}${headingClass}"${headingAttr}>
                    <span class="regeneration-diff-line-number">${lineNumber || ''}</span>
                    <span class="regeneration-diff-marker">${marker}</span>
                    <span class="regeneration-diff-text">${escapeResultHtml(text) || '&nbsp;'}</span>
                </div>
            `;
        }

        function renderRegenerationComparison(originalMarkdown, regeneratedMarkdown) {
            const container = document.getElementById('regenContent');
            if (!container) return;

            const comparison = buildRegenerationLineDiff(originalMarkdown, regeneratedMarkdown);
            const changedLineCount = comparison.deletedLineCount + comparison.insertedLineCount;
            const rows = comparison.lines.slice(0, 900);
            const truncated = comparison.lines.length > rows.length;

            if (!changedLineCount) {
                container.innerHTML = '<div class="info-box">Перегенерированная версия не отличается от исходной.</div>';
                return;
            }

            container.innerHTML = `
                <div class="regeneration-compare-view">
                    <div class="regeneration-compare-summary">
                        <strong>Сравнение README</strong>
                        <span>Исходный: ${comparison.originalLineCount} строк</span>
                        <span>Перегенерированный: ${comparison.regeneratedLineCount} строк</span>
                        <span>Добавлено/изменено: ${comparison.insertedLineCount}</span>
                        <span>Удалено: ${comparison.deletedLineCount}</span>
                    </div>
                    <div class="regeneration-compare-document" role="document" aria-label="Текстовое сравнение README">
                        ${rows.map(renderRegenerationDiffLine).join('')}
                    </div>
                    ${truncated ? '<div class="regeneration-compare-note">Показаны первые 900 строк сравнения. Полный текст доступен в режиме Markdown.</div>' : ''}
                </div>
            `;
        }

        function renderRegenerationReadme(markdown = null) {
            const container = document.getElementById('regenContent');
            if (!container) return;

            const state = getResultRuntimeState();
            const regeneratedMarkdown = markdown || getRegeneratedMarkdownFromState(state);
            if (!regeneratedMarkdown) {
                container.innerHTML = '<div class="info-box">Перегенерированная версия появится здесь после запуска перегенерации.</div>';
                renderReadmeToc('', { tocId: 'regenToc', contentId: 'regenContent' });
                return;
            }

            renderReadmeToc(regeneratedMarkdown, { tocId: 'regenToc', contentId: 'regenContent' });
            if (isResultReadmeComparisonActive()) {
                renderRegenerationComparison(getOriginalMarkdownForComparison(state), regeneratedMarkdown);
                return;
            }

            renderMarkdownByMode('regenContent', regeneratedMarkdown);
        }

        function isMethodologyReviewEnabledForResults() {
            const state = getResultRuntimeState();
            const capabilities = state.workflowCapabilities || state.workflowProfile?.capabilities || {};
            if (Object.prototype.hasOwnProperty.call(capabilities, 'stage_review')) {
                return Boolean(capabilities.stage_review);
            }
            return Boolean(document.getElementById('methodologyHumanReview')?.checked);
        }

        function isProjectRegenerationEnabledForResults() {
            const state = getResultRuntimeState();
            const capabilities = state.workflowCapabilities || state.workflowProfile?.capabilities || {};
            if (Object.prototype.hasOwnProperty.call(capabilities, 'project_regeneration')) {
                return Boolean(capabilities.project_regeneration);
            }
            return !isMethodologyReviewEnabledForResults();
        }

function extractReadmeToc(markdown) {
            const toc = [];
            String(markdown || '').split(/\r?\n/).forEach((line) => {
                const match = line.match(/^(#{1,3})\s+(.+?)\s*$/);
                if (!match) return;
                const title = match[2].replace(/[#*_`]/g, '').trim();
                if (!title || /^содержание$/i.test(title)) return;
                toc.push({
                    level: match[1].length,
                    title,
                    key: `${match[1].length}-${title.toLowerCase().replace(/\s+/g, '-')}-${toc.length}`
                });
            });
            return toc.slice(0, 18);
        }

        function extractMarkdownHeadingTitle(line) {
            const match = String(line || '').match(/^(#{1,3})\s+(.+?)\s*#*\s*$/);
            if (!match) return null;
            const title = match[2].replace(/[#*_`]/g, '').trim();
            return title ? { level: match[1].length, title } : null;
        }

        function escapeResultHtml(value) {
            if (typeof escapeHtmlSafe === 'function') {
                return escapeHtmlSafe(value);
            }
            return String(value || '')
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#039;');
        }

        function excerptSectionText(lines) {
            return String(lines.join(' ') || '')
                .replace(/```[\s\S]*?```/g, ' ')
                .replace(/[#*_`>|-]/g, ' ')
                .replace(/\s+/g, ' ')
                .trim()
                .slice(0, 220);
        }

        function extractRegenerationSections(markdown) {
            const text = String(markdown || '');
            if (!text.trim()) return [];

            const lines = text.split(/\r?\n/);
            const sections = [];
            let current = null;

            const finish = (endLine) => {
                if (!current) return;
                const excerpt = excerptSectionText(current.lines);
                const stateKey = regenerationSectionStateKey({
                    level: current.level,
                    title: current.title,
                    startLine: current.startLine,
                    endLine,
                });
                sections.push({
                    id: `section-${sections.length + 1}`,
                    stateKey,
                    level: current.level,
                    title: current.title,
                    startLine: current.startLine,
                    endLine,
                    excerpt,
                });
            };

            lines.forEach((line, index) => {
                const match = line.match(/^(#{1,3})\s+(.+?)\s*#*\s*$/);
                if (match) {
                    finish(index);
                    current = {
                        level: match[1].length,
                        title: match[2].replace(/[#*_`]/g, '').trim(),
                        startLine: index + 1,
                        lines: [],
                    };
                    return;
                }
                if (current) {
                    current.lines.push(line);
                }
            });
            finish(lines.length);

            if (!sections.length) {
                return [{
                    id: 'section-1',
                    stateKey: regenerationSectionStateKey({
                        level: 1,
                        title: 'Весь README',
                        startLine: 1,
                        endLine: lines.length,
                    }),
                    level: 1,
                    title: 'Весь README',
                    startLine: 1,
                    endLine: lines.length,
                    excerpt: excerptSectionText(lines),
                }];
            }

            return sections.slice(0, 32);
        }

        function regenerationSectionStateKey(section) {
            return [
                section.level || 0,
                String(section.title || '').trim().toLowerCase(),
                section.startLine || 0,
                section.endLine || 0,
            ].join('|');
        }

        function regenerationInstructionKey(instruction) {
            return [
                instruction.title,
                instruction.change,
                instruction.keep || '',
            ].map(value => String(value || '').trim().toLowerCase()).join('|');
        }

        function formatRegenerationInstruction(instruction, index, prefix = 'Правка') {
            return [
                `${prefix} ${index + 1}: ${instruction.title}`,
                instruction.startLine && instruction.endLine ? `Диапазон строк: ${instruction.startLine}-${instruction.endLine}` : '',
                `Что исправить: ${instruction.change}`,
                instruction.keep ? `Что оставить: ${instruction.keep}` : '',
            ].filter(Boolean).join('\n');
        }

        function getSelectedRegenerationInstructions() {
            return selectedRegenerationSections().map(section => ({
                title: section.title,
                startLine: section.startLine,
                endLine: section.endLine,
                change: section.change,
                keep: section.keep,
                appliedAt: null,
            }));
        }

        function saveRegenerationSectionDrafts() {
            const sections = getRegenerationSections();
            if (!sections.length) return;
            const drafts = { ...getRegenerationSectionDrafts() };
            sections.forEach((section) => {
                const checkbox = document.querySelector(`[data-regen-section-id="${section.id}"]`);
                const change = String(document.querySelector(`[data-regen-change-for="${section.id}"]`)?.value || '').trim();
                const keep = String(document.querySelector(`[data-regen-keep-for="${section.id}"]`)?.value || '').trim();
                const selected = Boolean(checkbox?.checked);
                if (selected || change || keep) {
                    drafts[section.stateKey] = { selected, change, keep };
                } else {
                    delete drafts[section.stateKey];
                }
            });
            setRegenerationStoreState({ sectionDrafts: drafts });
        }

        function updateRegenerationSectionDrafts() {
            saveRegenerationSectionDrafts();
            renderRegenerationSectionComments();
        }

        function rememberRegenerationInstructions(instructions) {
            const next = Array.isArray(instructions) ? instructions : [];
            if (!next.length) return;
            const history = [...getRegenerationInstructionHistory()];
            const known = new Set(history.map(regenerationInstructionKey));
            next.forEach((instruction) => {
                if (!instruction?.change) return;
                const normalized = {
                    title: instruction.title || 'Часть README',
                    startLine: instruction.startLine || null,
                    endLine: instruction.endLine || null,
                    change: String(instruction.change || '').trim(),
                    keep: String(instruction.keep || '').trim(),
                    appliedAt: new Date().toISOString(),
                };
                const key = regenerationInstructionKey(normalized);
                if (known.has(key)) return;
                known.add(key);
                history.push(normalized);
            });
            setRegenerationStoreState({ instructionHistory: history });
            renderRegenerationSectionComments();
        }

        function renderRegenerationSectionComments() {
            const container = document.getElementById('regenerationSectionComments');
            if (!container) return;
            const selected = selectedRegenerationSections();
            const history = getRegenerationInstructionHistory();
            const globalComment = String(document.getElementById('regenerationGlobalComment')?.value || '').trim();
            if (!selected.length && !history.length && !globalComment) {
                container.innerHTML = '<div class="regeneration-empty-note">Выберите одну или несколько частей README. Для каждой выбранной части появятся поля правки.</div>';
                return;
            }
            const globalBlock = globalComment
                ? `
                    <div class="regeneration-section-note compact">
                        <strong>Общая правка</strong>
                        <p>${escapeResultHtml(globalComment)}</p>
                    </div>
                `
                : '';
            const selectedBlock = selected.length
                ? `
                    <div class="regeneration-section-note compact">
                        <strong>В следующую перегенерацию попадёт: ${selected.length}</strong>
                        <div class="regeneration-section-tags">${selected.map(item => `<span>${escapeResultHtml(item.title)}</span>`).join('')}</div>
                    </div>
                `
                : '';
            const historyBlock = history.length
                ? `
                    <div class="regeneration-history-note">
                        <strong>Уже применённые правки: ${history.length}</strong>
                        <p>Они будут учитываться при следующей перегенерации как сохранённые изменения, если не противоречат новым правкам.</p>
                        <div class="regeneration-history-list">
                            ${history.map((item, index) => `
                                <article>
                                    <b>${index + 1}. ${escapeResultHtml(item.title)}</b>
                                    <span>${escapeResultHtml(item.change)}</span>
                                    ${item.keep ? `<small>Оставить: ${escapeResultHtml(item.keep)}</small>` : ''}
                                </article>
                            `).join('')}
                        </div>
                    </div>
                `
                : '';
            container.innerHTML = `
                ${globalBlock}
                ${selectedBlock}
                ${historyBlock}
            `;
        }

        function renderRegenerationSectionSelector(markdown) {
            const selector = document.getElementById('regenerationSectionSelector');
            if (!selector) return;

            saveRegenerationSectionDrafts();
            const runtimeMarkdown = markdown || getResultRuntimeState().currentMarkdown || '';
            const regenerationSections = extractRegenerationSections(runtimeMarkdown);
            setRegenerationStoreState({ sections: regenerationSections });

            if (!regenerationSections.length) {
                selector.innerHTML = '<div class="info-box">Сначала сгенерируйте README, чтобы выбрать разделы для перегенерации.</div>';
                renderRegenerationSectionComments();
                return;
            }

            selector.innerHTML = regenerationSections.map((section) => {
                const checkboxId = `regen-${section.id}`;
                const changeId = `regen-change-${section.id}`;
                const keepId = `regen-keep-${section.id}`;
                const levelClass = `level-${section.level}`;
                const draft = getRegenerationSectionDrafts()[section.stateKey] || {};
                const isSelected = Boolean(draft.selected);
                return `
                    <article class="regeneration-section-option ${levelClass}${isSelected ? ' is-selected' : ''}" data-regen-section-card="${section.id}">
                        <label class="regeneration-section-row" for="${checkboxId}">
                            <input type="checkbox" id="${checkboxId}" value="${section.id}" data-regen-section-id="${section.id}" onchange="toggleRegenerationSectionForm('${section.id}', this.checked)"${isSelected ? ' checked' : ''}>
                            <span>
                                <strong>${escapeResultHtml(section.title)}</strong>
                                <small>строки ${section.startLine}-${section.endLine}</small>
                            </span>
                        </label>
                        <div class="regeneration-section-form" data-regen-section-form="${section.id}"${isSelected ? '' : ' hidden'}>
                            <div class="regeneration-section-field">
                                <label for="${changeId}">Что исправить</label>
                                <textarea id="${changeId}" rows="2" data-regen-change-for="${section.id}" placeholder="Что именно изменить в этой части README..." oninput="updateRegenerationSectionDrafts()">${escapeResultHtml(draft.change || '')}</textarea>
                            </div>
                            <div class="regeneration-section-field">
                                <label for="${keepId}">Что оставить <span>опционально</span></label>
                                <textarea id="${keepId}" rows="2" data-regen-keep-for="${section.id}" placeholder="Что важно сохранить без изменений..." oninput="updateRegenerationSectionDrafts()">${escapeResultHtml(draft.keep || '')}</textarea>
                            </div>
                        </div>
                    </article>
                `;
            }).join('');

            renderRegenerationSectionComments();
        }

        function toggleRegenerationSectionForm(sectionId, checked) {
            const card = document.querySelector(`[data-regen-section-card="${sectionId}"]`);
            const form = document.querySelector(`[data-regen-section-form="${sectionId}"]`);
            if (card) card.classList.toggle('is-selected', Boolean(checked));
            if (form) form.hidden = !checked;
            saveRegenerationSectionDrafts();
            renderRegenerationSectionComments();
        }

        function selectedRegenerationSections() {
            const selectedIds = [...document.querySelectorAll('[data-regen-section-id]:checked')]
                .map(input => input.value);
            return getRegenerationSections()
                .filter(section => selectedIds.includes(section.id))
                .map(section => ({
                    ...section,
                    change: String(document.querySelector(`[data-regen-change-for="${section.id}"]`)?.value || '').trim(),
                    keep: String(document.querySelector(`[data-regen-keep-for="${section.id}"]`)?.value || '').trim(),
                }));
        }

        function clearRegenerationSectionComments(options = {}) {
            const clearHistory = options.clearHistory !== false;
            document.querySelectorAll('[data-regen-section-id]:checked').forEach(input => {
                input.checked = false;
                toggleRegenerationSectionForm(input.value, false);
            });
            document.querySelectorAll('[data-regen-change-for], [data-regen-keep-for]').forEach(field => {
                field.value = '';
            });
            if (clearHistory) {
                setRegenerationStoreState({ instructionHistory: [] });
            }
            setRegenerationStoreState({ sectionDrafts: {} });
            const globalField = document.getElementById('regenerationGlobalComment');
            if (globalField) globalField.value = '';
            renderRegenerationSectionComments();
        }

        function buildRegenerationComments() {
            const baseComment = String(document.getElementById('regenerationComments')?.value || '').trim();
            const globalComment = String(document.getElementById('regenerationGlobalComment')?.value || '').trim();
            const selected = selectedRegenerationSections();
            const incomplete = selected.filter(section => !section.change);
            if (incomplete.length) {
                alert('Для каждой выбранной части заполните поле «Что исправить».');
                return null;
            }
            const historyComments = getRegenerationInstructionHistory().map((section, index) => {
                return formatRegenerationInstruction(section, index, 'Сохранённая правка');
            });
            const scopedComments = selected.map((section, index) => formatRegenerationInstruction(section, index));
            const historyHeader = historyComments.length
                ? 'Уже применённые правки предыдущих перегенераций. Сохрани их в новой версии, если они не противоречат текущим правкам:'
                : '';
            const scopedHeader = scopedComments.length
                ? 'Текущие правки имеют приоритет. Учитывай все инструкции вместе; для непомеченных частей сохраняй текущую структуру и смысл без лишних изменений.'
                : '';
            return [
                baseComment,
                globalComment ? `Общая правка:\n${globalComment}` : '',
                historyHeader,
                ...historyComments,
                scopedHeader,
                ...scopedComments,
            ].filter(Boolean).join('\n\n');
        }

        function renderReadmeToc(markdown, options = {}) {
            const tocId = options.tocId || 'readmeToc';
            const contentId = options.contentId || 'readmeContent';
            const aside = document.getElementById(tocId);
            if (!aside) return;
            const toc = extractReadmeToc(markdown);
            aside.innerHTML = '';

            const title = document.createElement('div');
            title.className = 'readme-toc-title';
            title.textContent = 'СОДЕРЖАНИЕ';
            aside.appendChild(title);

            if (!toc.length) {
                const empty = document.createElement('div');
                empty.className = 'readme-toc-empty';
                empty.textContent = 'Заголовки появятся после рендера README.';
                aside.appendChild(empty);
                return;
            }

            toc.forEach((item, index) => {
                const button = document.createElement('button');
                button.type = 'button';
                button.className = `readme-toc-link level-${item.level}${index === 0 ? ' active' : ''}`;
                button.textContent = item.title;
                button.dataset.heading = item.title;
                button.addEventListener('click', () => scrollReadmeToHeading(item.title, button, { tocId, contentId }));
                aside.appendChild(button);
            });
        }

        function scrollReadmeToHeading(title, activeButton = null, options = {}) {
            const tocId = options.tocId || 'readmeToc';
            const contentId = options.contentId || 'readmeContent';
            const container = document.getElementById(contentId);
            if (!container) return;
            const normalized = String(title || '').trim();
            const heading = [...container.querySelectorAll('h1, h2, h3, [data-heading]')]
                .find((node) => {
                    const headingText = node.dataset?.heading || node.textContent || '';
                    return String(headingText).trim() === normalized;
                });
            if (heading) {
                heading.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
            document.getElementById(tocId)?.querySelectorAll('.readme-toc-link').forEach((btn) => btn.classList.remove('active'));
            if (activeButton) activeButton.classList.add('active');
        }

        function renderResultReadme(markdown) {
            const container = document.getElementById('readmeContent');
            if (!container) return;
            renderMarkdownByMode('readmeContent', markdown);
            renderReadmeToc(markdown, { tocId: 'readmeToc', contentId: 'readmeContent' });
            renderRegenerationSectionSelector(markdown);
        }

        function setReadmeRenderMode(mode) {
            setResultStoreState({
                readmeRenderMode: mode === 'preview' ? 'preview' : 'markdown',
                readmeComparisonActive: false,
            });
            updateReadmeModeButtons();
            const state = getResultRuntimeState();
            const activeTab = getActiveResultTabName();
            if (activeTab === 'regen') {
                renderRegenerationReadme();
                return;
            }
            const { currentMarkdown } = state;
            if (currentMarkdown) {
                renderResultReadme(currentMarkdown);
            }
        }

        function compareCurrentResult() {
            if (!isProjectRegenerationEnabledForResults()) {
                showTab('metrics', document.querySelector('.result-tabs .tab[onclick*="metrics"]'));
                return;
            }
            const state = getResultRuntimeState();
            const regeneratedMarkdown = getRegeneratedMarkdownFromState(state);
            const activeTab = getActiveResultTabName();
            if (regeneratedMarkdown) {
                setResultStoreState({ readmeComparisonActive: true });
                updateReadmeModeButtons();
                showTab('regen', document.querySelector('.result-tabs .tab[onclick*="regen"]'));
                renderRegenerationReadme(regeneratedMarkdown);
                return;
            }
            if (activeTab === 'regen') {
                setResultStoreState({ readmeComparisonActive: false });
                updateReadmeModeButtons();
                const container = document.getElementById('regenContent');
                if (container) {
                    container.innerHTML = '<div class="info-box">Для сравнения сначала запустите перегенерацию README.</div>';
                }
                return;
            }
            const { regeneratedRubric, regeneratedTextStats } = state;
            if (regeneratedRubric || regeneratedTextStats) {
                showTab('regen', document.querySelector('.result-tabs .tab[onclick*="regen"]'));
                renderRegenerationReadme();
                return;
            }
            showTab('metrics', document.querySelector('.result-tabs .tab[onclick*="metrics"]'));
        }

        function openRegenerationFromMetrics() {
            if (!isProjectRegenerationEnabledForResults()) {
                return;
            }
            window.fillCommentsFromFailedCriteria?.();
            showTab('regen', document.querySelector('.result-tabs .tab[onclick*="regen"]'));
            document.getElementById('regenerationComments')?.focus();
        }

        function activateResultTab(tabName) {
            const tabButton = [...document.querySelectorAll('#resultsArea .result-tabs .tab, #resultsArea > .tabs .tab')]
                .find(tab => {
                    const handler = tab.getAttribute('onclick') || '';
                    return handler.includes(`'${tabName}'`) || handler.includes(`"${tabName}"`);
                });
            if (tabButton) {
                showTab(tabName, tabButton);
            }
        }

        function extractPracticeTasksFromMarkdown(markdown) {
            const text = String(markdown || '');
            if (!text.trim()) return [];

            const chapterStart = text.search(/^##\s+Глава\s+3/im);
            let scope = text;
            if (chapterStart >= 0) {
                const rest = text.slice(chapterStart);
                const nextChapter = rest.slice(1).search(/^##\s+/im);
                scope = nextChapter >= 0 ? rest.slice(0, nextChapter + 1) : rest;
            }
            const headingRe = /^#{3,4}\s+(.+?)\s*$/gm;
            const matches = [...scope.matchAll(headingRe)]
                .filter(match => /(?:задач|task|упражнен)/i.test(match[1] || '') || /^\d+(?:\.\d+)+\.?\s+/.test(match[1] || ''));

            return matches.slice(0, 12).map((match, index) => {
                const next = matches[index + 1];
                const bodyStart = match.index + match[0].length;
                const bodyEnd = next ? next.index : scope.length;
                const body = scope.slice(bodyStart, bodyEnd).replace(/```[\s\S]*?```/g, ' ').replace(/\s+/g, ' ').trim();
                return {
                    title: match[1].replace(/[#*_`]/g, '').trim() || `Задача ${index + 1}`,
                    excerpt: body.slice(0, 240),
                };
            });
        }

function renderGeneratedDataTab(result) {
            const container = document.getElementById('generatedDataContent');
            if (!container) return;

            const assets = result?.assets || result?.report_json?.assets || {};
            const files = Array.isArray(assets.files) ? assets.files.filter(Boolean) : [];
            const runtimeMarkdown = result?.markdown || result?.report_json?.markdown || getResultRuntimeState().currentMarkdown || '';
            const tasks = extractPracticeTasksFromMarkdown(runtimeMarkdown);
            if (!files.length && !tasks.length) {
                container.innerHTML = '<div>Сгенерированные файлы данных и практические задачи отсутствуют. Если задания ссылаются на materials/data, они появятся здесь после генерации.</div>';
                return;
            }

            const taskItems = tasks.map((task, index) => `
                <article class="generated-task-data-item">
                    <b>${index + 1}. ${escapeResultHtml(task.title)}</b>
                    ${task.excerpt ? `<p>${escapeResultHtml(task.excerpt)}</p>` : ''}
                </article>
            `).join('');
            const taskBlock = taskItems
                ? `
                    <section class="generated-task-data">
                        <h4>Практические задачи: ${tasks.length}</h4>
                        <div class="generated-task-data-list">${taskItems}</div>
                    </section>
                `
                : '';

            const items = files.map((file, index) => {
                const path = String(file.path || file.name || `file-${index + 1}`).trim();
                const decoded = decodeGeneratedAssetText(file);
                const sizeLabel = formatGeneratedAssetSize(file.data);
                const escapedPath = window.sanitize ? window.sanitize.escapeHtml(path) : path;
                const escapedSize = window.sanitize ? window.sanitize.escapeHtml(sizeLabel) : sizeLabel;
                const preview = decoded
                    ? renderGeneratedAssetPreview(decoded)
                    : '<div class="generated-data-empty">Предпросмотр недоступен для бинарного файла. Файл будет в архиве.</div>';
                return `
                    <details class="generated-data-item" ${index === 0 ? 'open' : ''}>
                        <summary>
                            <span class="generated-data-path">${escapedPath}</span>
                            <span class="generated-data-size">${escapedSize}</span>
                        </summary>
                        ${preview}
                    </details>
                `;
            }).join('');

            const fileBlock = files.length
                ? `<div class="generated-data-summary">Файлы, которые попадут в архив вместе с README: ${files.length}.</div><div class="generated-data-list">${items}</div>`
                : '<div class="generated-data-empty">Файлы materials/data отсутствуют для текущей версии README.</div>';
            if (window.sanitize) {
                window.sanitize.safeSetHTML(container, taskBlock + fileBlock);
            } else {
                container.innerHTML = taskBlock + fileBlock;
            }
        }

        function renderGeneratedAssetPreview(text) {
            const previewText = String(text || '').slice(0, 4000);
            const escaped = window.sanitize ? window.sanitize.escapeHtml(previewText) : previewText;
            const suffix = String(text || '').length > previewText.length
                ? '<div class="generated-data-empty">Показан фрагмент первых 4000 символов.</div>'
                : '';
            return `<pre class="generated-data-preview">${escaped}</pre>${suffix}`;
        }

        function decodeGeneratedAssetText(file) {
            const path = String(file?.path || file?.name || '');
            if (!/\.(md|markdown|txt|csv|json|yaml|yml|xml|html|htm|js|ts|py|sql|ini|toml)$/i.test(path)) {
                return '';
            }
            try {
                const raw = atob(String(file.data || ''));
                const bytes = Uint8Array.from(raw, char => char.charCodeAt(0));
                const text = new TextDecoder('utf-8', { fatal: false }).decode(bytes);
                const controlChars = (text.match(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g) || []).length;
                if (text.length && controlChars / text.length > 0.04) {
                    return '';
                }
                return text;
            } catch (_error) {
                return '';
            }
        }

        function formatGeneratedAssetSize(base64Data) {
            const value = String(base64Data || '');
            if (!value) return '0 Б';
            const padding = (value.match(/=+$/) || [''])[0].length;
            const bytes = Math.max(0, Math.floor(value.length * 3 / 4) - padding);
            if (bytes < 1024) return `${bytes} Б`;
            if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} КБ`;
            return `${(bytes / (1024 * 1024)).toFixed(1)} МБ`;
        }

        if (typeof window !== 'undefined') {
            Object.assign(window, {
                extractReadmeToc,
                renderReadmeToc,
                scrollReadmeToHeading,
                renderResultReadme,
                renderRegenerationReadme,
                renderRegenerationComparison,
                buildRegenerationLineDiff,
                setReadmeRenderMode,
                compareCurrentResult,
                openRegenerationFromMetrics,
                activateResultTab,
                renderRegenerationSectionComments,
                renderRegenerationSectionSelector,
                toggleRegenerationSectionForm,
                updateRegenerationSectionDrafts,
                getSelectedRegenerationInstructions,
                rememberRegenerationInstructions,
                clearRegenerationSectionComments,
                buildRegenerationComments,
                extractPracticeTasksFromMarkdown,
                renderGeneratedDataTab,
                renderGeneratedAssetPreview,
                decodeGeneratedAssetText,
                formatGeneratedAssetSize,
            });
        }

