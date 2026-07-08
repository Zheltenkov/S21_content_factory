// Translation screen controller.
// Keeps README/video translation state out of main.js while preserving legacy window functions.

        function setTranslationText(id, value) {
            const el = document.getElementById(id);
            if (el) {
                el.textContent = value == null ? '' : String(value);
            }
        }

        function getTranslationApiUrl() {
            return window.API_URL || window.ContentGenApiUrl || '/api/v1';
        }

        function getTranslationAuthHeaders() {
            return typeof window.getAuthHeaders === 'function' ? window.getAuthHeaders() : {};
        }

        const TRANSLATION_PHASE_LABELS = {
            translate: 'Перевод...',
            refine: 'Улучшение читаемости...',
            combine: 'Объединение версий...',
            repair: 'Починка непереведённых секций...',
            validate: 'Проверка структуры и языка...',
            repair_retry_1: 'Повторный перевод (попытка 2)...',
            repair_retry_2: 'Повторный перевод (попытка 3)...',
            build_docx: 'Сборка DOCX...',
            queued: 'В очереди...',
            extract_audio: 'Извлечение аудио...',
            chunk_audio: 'Разбиение аудио...',
            transcribe: 'Транскрипция (RU)...',
            correct_asr: 'Коррекция распознавания...',
            build_subtitles: 'Формирование субтитров...',
            render_video: 'Рендер видео с субтитрами...',
            done: 'Готово',
        };

        const TRANSLATION_PHASE_PROGRESS = {
            queued: 0,
            extract_audio: 10,
            chunk_audio: 15,
            transcribe: 35,
            correct_asr: 45,
            translate: 60,
            refine: 50,
            repair: 65,
            validate: 80,
            repair_retry_1: 40,
            repair_retry_2: 60,
            build_docx: 92,
            build_subtitles: 75,
            build_srt: 90,
            render_video: 90,
            combine: 100,
            done: 100,
        };

        function normalizeTranslationProgress(value, fallback = 0) {
            const numeric = Number(value);
            if (!Number.isFinite(numeric)) return fallback;
            return Math.max(0, Math.min(100, Math.round(numeric)));
        }

        function translationPhaseLabel(phase) {
            return TRANSLATION_PHASE_LABELS[phase] || 'Выполняется...';
        }

        function translationDisplayLabel(phase, status) {
            if (status === 'completed') return 'Готово';
            if (status === 'failed') return 'Ошибка обработки';
            return translationPhaseLabel(phase).replace(/\.\.\.$/, '');
        }

        function currentTranslationLanguageCode() {
            const langSelect = document.getElementById('translationLanguage');
            return (langSelect && langSelect.value ? langSelect.value : 'en').toUpperCase();
        }

        function syncTranslationLanguageBadges() {
            const language = currentTranslationLanguageCode();
            setTranslationText('translationSummaryLanguage', `RU → ${language}`);
            setTranslationText('translationTargetLanguageBadge', `✓ ${language} · ПЕРЕВОД`);
            const mirror = document.getElementById('translationLanguageMirror');
            const source = document.getElementById('translationLanguage');
            if (mirror && source && mirror.value !== source.value) {
                mirror.value = source.value || 'en';
            }
            return language;
        }

        function updateTranslationSummary(kind = 'document', statusText = 'Готово') {
            const language = syncTranslationLanguageBadges();
            setTranslationText('translationSummaryMode', kind === 'video' ? 'Видео' : 'Документ');
            setTranslationText('translationSummaryStatus', statusText);
            const brandBadge = document.getElementById('translationBrandBadge');
            const brandMark = document.getElementById('translationBrandMark');
            const subbarTitle = document.getElementById('translationSubbarTitle');
            if (brandBadge) brandBadge.setAttribute('data-step', kind === 'video' ? '05.2' : '05.1');
            if (brandMark) {
                brandMark.textContent = kind === 'video'
                    ? `ПЕРЕВОД ВИДЕО · ${statusText || 'В РАБОТЕ'}`
                    : `ПЕРЕВОД ДОКУМЕНТА · RU → ${language}`;
            }
            if (subbarTitle) subbarTitle.textContent = kind === 'video' ? 'Перевод · Видео' : 'Перевод';
        }

        let translationOriginalMarkdown = '';
        let translationTranslatedMarkdown = '';
        let translationFileName = 'README_translated.md';
        let translationCurrentRequestId = null;
        let translationJobType = 'readme';
        let translationRenderAsMarkdown = false;
        let translationSelectedDocumentFile = null;
        let translationDocumentDownloadType = 'markdown';
        const TRANSLATION_VIDEO_DOWNLOAD_ORDER = ['video', 'vtt', 'srt', 'ass', 'transcript'];
        const TRANSLATION_VIDEO_DOWNLOAD_LABELS = {
            video: 'Скачать видео с переводом',
            vtt: 'VTT',
            srt: 'SRT',
            ass: 'ASS',
            transcript: 'Транскрипт RU (JSON)'
        };
        const TRANSLATION_CLIENT_READABLE_EXTENSIONS = new Set(['.md', '.markdown', '.txt', '.html', '.htm']);
        const TRANSLATION_MARKDOWN_EXTENSIONS = new Set(['.md', '.markdown']);

        function getTranslationFileExtension(file) {
            const name = String(file?.name || '').toLowerCase();
            const dot = name.lastIndexOf('.');
            return dot >= 0 ? name.slice(dot) : '';
        }

        function isClientReadableTranslationFile(file) {
            return TRANSLATION_CLIENT_READABLE_EXTENSIONS.has(getTranslationFileExtension(file));
        }

        function isMarkdownTranslationFile(file) {
            return TRANSLATION_MARKDOWN_EXTENSIONS.has(getTranslationFileExtension(file));
        }

        function translatedDocumentFileName(file) {
            const baseName = String(file?.name || 'document').replace(/\.[^.]+$/, '') || 'document';
            return `${baseName}_translated.md`;
        }

        function extractHtmlPreviewText(rawHtml) {
            try {
                const doc = new DOMParser().parseFromString(String(rawHtml || ''), 'text/html');
                doc.querySelectorAll('script, style, noscript').forEach(node => node.remove());
                return (doc.body?.innerText || doc.documentElement?.innerText || rawHtml || '').trim();
            } catch {
                return String(rawHtml || '').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();
            }
        }

        function countTranslationWords(text) {
            return String(text || '')
                .trim()
                .split(/\s+/)
                .filter(Boolean).length;
        }

        function formatTranslationBytes(bytes) {
            const value = Number(bytes || 0);
            if (!value) return '0 КБ';
            if (value >= 1024 * 1024) return `${Math.round(value / 1024 / 1024)} МБ`;
            return `${Math.max(1, Math.round(value / 1024))} КБ`;
        }

        function updateTranslationTextMeta(kind, text, bytes) {
            const words = countTranslationWords(text);
            const suffix = bytes ? ` · ${formatTranslationBytes(bytes)}` : '';
            const value = `${words.toLocaleString('ru-RU')} слов${suffix}`;
            const targetId = kind === 'translated' ? 'translationTranslatedMeta' : 'translationOriginalMeta';
            setTranslationText(targetId, value);
            return value;
        }

        function displayPlainText(text, containerId) {
            const container = document.getElementById(containerId);
            if (!container) return;
            if (!text || typeof text !== 'string') {
                container.innerHTML = '<div class="info-box">Контент отсутствует</div>';
                return;
            }
            const div = document.createElement('div');
            div.style.whiteSpace = 'pre-wrap';
            div.style.wordBreak = 'break-word';
            div.style.fontFamily = 'inherit';
            div.textContent = text;
            container.innerHTML = '';
            container.appendChild(div);
        }

        function resetTranslationState() {
            if (typeof stopTranslationPolling === 'function') stopTranslationPolling();
            translationOriginalMarkdown = '';
            translationTranslatedMarkdown = '';
            translationFileName = 'README_translated.md';
            translationCurrentRequestId = null;
            translationJobType = 'readme';
            translationRenderAsMarkdown = false;
            translationSelectedDocumentFile = null;
            translationDocumentDownloadType = 'markdown';

            const mdBtn = document.getElementById('downloadTranslatedMarkdownBtn');
            if (mdBtn) {
                mdBtn.style.display = 'inline-block';
                mdBtn.textContent = '↓ Скачать';
                mdBtn.onclick = downloadTranslatedMarkdown;
            }

            const noResults = document.getElementById('translationNoResults');
            const resultsArea = document.getElementById('translationResultsArea');
            const originalContainer = document.getElementById('translationOriginalContent');
            const translatedContainer = document.getElementById('translationTranslatedContent');

            if (noResults) {
                noResults.style.display = 'block';
            }
            if (resultsArea) {
                resultsArea.style.display = 'none';
            }
            if (originalContainer) {
                originalContainer.innerHTML = '';
            }
            if (translatedContainer) {
                translatedContainer.innerHTML = '';
            }
            setTranslationText('translationSourceFileTitle', 'Загрузить README или документ');
            setTranslationText('translationFileName', 'Документ для перевода');
            setTranslationText('translationOriginalMeta', '0 слов');
            setTranslationText('translationTranslatedMeta', '0 слов');
            resetTranslationUploadProgress();
            resetTranslationVideoProgress(false);
            resetTranslationVideoResultPanel();
            const brandBadge = document.getElementById('translationBrandBadge');
            const brandMark = document.getElementById('translationBrandMark');
            if (brandBadge) brandBadge.setAttribute('data-step', '05.1');
            syncTranslationLanguageBadges();
            if (brandMark) brandMark.textContent = `ПЕРЕВОД ДОКУМЕНТА · RU → ${currentTranslationLanguageCode()}`;
            updateTranslationSummary('document', 'Ожидает запуска');
        }

        function handleTranslationFileSelect(event) {
            const fileInput = event.target;
            const file = fileInput && fileInput.files && fileInput.files[0];
            const fileNameLabel = document.getElementById('translationFileName');

            if (!file) {
                translationSelectedDocumentFile = null;
                if (fileNameLabel) {
                    fileNameLabel.textContent = 'Документ для перевода';
                }
                setTranslationText('translationSourceFileTitle', 'Загрузить README или документ');
                return;
            }

            translationSelectedDocumentFile = file;
            const sourceTitle = document.getElementById('translationSourceFileTitle');
            if (sourceTitle) sourceTitle.textContent = file.name;
            if (fileNameLabel) fileNameLabel.textContent = `${formatTranslationBytes(file.size)} · загружен`;

            const lowerFileName = (file.name || '').toLowerCase();
            translationRenderAsMarkdown = isMarkdownTranslationFile(file);
            translationFileName = translatedDocumentFileName(file);

            if (!isClientReadableTranslationFile(file)) {
                translationOriginalMarkdown = '';
                const input = document.getElementById('translationInput');
                if (input) {
                    input.value = '';
                }
                const noResults = document.getElementById('translationNoResults');
                const resultsArea = document.getElementById('translationResultsArea');
                const originalContainer = document.getElementById('translationOriginalContent');
                const translatedContainer = document.getElementById('translationTranslatedContent');
                if (noResults && resultsArea) {
                    noResults.style.display = 'none';
                    resultsArea.style.display = 'block';
                }
                if (originalContainer) {
                    originalContainer.innerHTML = '';
                    const box = document.createElement('div');
                    box.className = 'info-box';
                    box.textContent = `${file.name}: текст будет извлечён на сервере перед переводом.`;
                    originalContainer.appendChild(box);
                }
                if (translatedContainer) {
                    translatedContainer.innerHTML = '<div class="info-box">Перевод появится после обработки документа.</div>';
                }
                setTranslationText('translationOriginalMeta', formatTranslationBytes(file.size));
                setTranslationText('translationTranslatedMeta', 'Ожидает перевода');
                updateTranslationSummary('document', 'Файл выбран');
                return;
            }

            const reader = new FileReader();
            reader.onload = function (e) {
                const rawText = e.target.result || '';
                const text = lowerFileName.endsWith('.html') || lowerFileName.endsWith('.htm')
                    ? extractHtmlPreviewText(rawText)
                    : String(rawText);
                translationOriginalMarkdown = String(text);

                const input = document.getElementById('translationInput');
                if (input) {
                    input.value = translationOriginalMarkdown;
                }

                const noResults = document.getElementById('translationNoResults');
                const resultsArea = document.getElementById('translationResultsArea');
                if (noResults && resultsArea) {
                    noResults.style.display = 'none';
                    resultsArea.style.display = 'block';
                }

                if (translationRenderAsMarkdown) {
                    displayMarkdown(translationOriginalMarkdown, 'translationOriginalContent');
                } else {
                    displayPlainText(translationOriginalMarkdown, 'translationOriginalContent');
                }
                updateTranslationTextMeta('original', translationOriginalMarkdown, file.size);
                setTranslationText('translationTranslatedMeta', 'Ожидает перевода');
                updateTranslationSummary('document', 'Предпросмотр');
            };
            reader.onerror = function (e) {
                console.error('Ошибка чтения файла для перевода:', e);
                alert('Ошибка чтения файла. Попробуйте выбрать другой файл.');
            };
            reader.readAsText(file);
        }

        let translationPollInterval = null;

        function setTranslationVideoProgress(label, percent, visible = true) {
            const container = document.getElementById('translationVideoProgress');
            const labelEl = document.getElementById('translationVideoProgressLabel');
            const pctEl = document.getElementById('translationVideoProgressPct');
            const barEl = document.getElementById('translationVideoProgressBar');
            if (!container || !labelEl || !pctEl || !barEl) return;
            const pct = normalizeTranslationProgress(percent);
            container.hidden = !visible;
            labelEl.textContent = label || 'Выполняется';
            pctEl.textContent = pct + ' %';
            barEl.style.width = pct + '%';
        }

        function resetTranslationVideoProgress(showIdle = false) {
            setTranslationVideoProgress(showIdle ? 'Готов к загрузке' : 'Ожидает запуска', 0, showIdle);
        }

        function resetTranslationUploadProgress() {
            const container = document.getElementById('translationUploadProgressContainer');
            const labelEl = document.getElementById('translationUploadProgressLabel');
            const barEl = document.getElementById('translationUploadProgressBar');
            if (!container || !labelEl || !barEl) return;
            container.style.display = 'none';
            labelEl.textContent = 'Загрузка видео: 0%';
            barEl.style.width = '0%';
        }

        function renderTranslationVideoDownloadButtons(container, resultLinks) {
            if (!container) return;
            container.innerHTML = '';
            TRANSLATION_VIDEO_DOWNLOAD_ORDER.forEach(function(type) {
                if (!resultLinks || !resultLinks[type]) return;
                const btn = document.createElement('button');
                btn.className = 'btn btn-download';
                btn.type = 'button';
                btn.textContent = TRANSLATION_VIDEO_DOWNLOAD_LABELS[type] || type;
                btn.onclick = function() { downloadTranslationArtifact(translationCurrentRequestId, type); };
                container.appendChild(btn);
            });
        }

        function resetTranslationVideoResultPanel() {
            const panel = document.getElementById('translationVideoResultPanel');
            const links = document.getElementById('translationVideoInlineDownloadLinks');
            if (panel) panel.hidden = true;
            if (links) links.innerHTML = '';
        }

        function activateTranslationVideoScreen() {
            const docRadio = document.getElementById('translationSourceDocument');
            const videoRadio = document.getElementById('translationSourceVideo');
            if (docRadio) docRadio.checked = false;
            if (videoRadio) videoRadio.checked = true;
            if (typeof window.toggleTranslationSourceMode === 'function') {
                window.toggleTranslationSourceMode();
            }
        }

        function showTranslationVideoResultPanel(job) {
            const panel = document.getElementById('translationVideoResultPanel');
            const title = document.getElementById('translationVideoResultTitle');
            const hint = document.getElementById('translationVideoResultHint');
            const links = document.getElementById('translationVideoInlineDownloadLinks');
            if (!panel) return;
            const resultLinks = job?.result_links || {};
            const hasDownloads = Object.keys(resultLinks).length > 0;
            panel.hidden = false;
            if (title) title.textContent = hasDownloads ? 'Субтитры и файлы перевода готовы' : 'Субтитры готовы';
            if (hint) {
                hint.textContent = hasDownloads
                    ? 'Скачайте нужные файлы здесь. Они относятся к обработанному видео.'
                    : 'Готовые файлы появятся здесь, когда сервер вернёт ссылки для скачивания.';
            }
            renderTranslationVideoDownloadButtons(links, resultLinks);
            if (!hasDownloads && links && translationCurrentRequestId) {
                const fallbackBtn = document.createElement('button');
                fallbackBtn.className = 'btn btn-download';
                fallbackBtn.type = 'button';
                fallbackBtn.textContent = 'Скачать субтитры';
                fallbackBtn.onclick = function() { downloadTranslatedSubtitles(); };
                links.appendChild(fallbackBtn);
            }
        }

        function updateTranslationUploadProgress(percent) {
            const container = document.getElementById('translationUploadProgressContainer');
            const labelEl = document.getElementById('translationUploadProgressLabel');
            const barEl = document.getElementById('translationUploadProgressBar');
            if (!container || !labelEl || !barEl) return;
            const pct = Math.max(0, Math.min(100, Math.round(percent || 0)));
            container.style.display = 'block';
            labelEl.textContent = 'Загрузка видео: ' + pct + '%';
            barEl.style.width = pct + '%';
        }

        function updateTranslationProgress(phase, status, progressArg) {
            const container = document.getElementById('translationProgressContainer');
            const phaseEl = document.getElementById('translationProgressPhase');
            const barEl = document.getElementById('translationProgressBar');
            if (!container || !phaseEl || !barEl) return;
            const videoModeActive = translationJobType === 'video' || !!document.getElementById('translationSourceVideo')?.checked;
            container.style.setProperty('display', videoModeActive ? 'none' : 'block', 'important');
            phaseEl.textContent = translationPhaseLabel(phase);
            const displayLabel = translationDisplayLabel(phase, status);
            updateTranslationSummary(translationJobType === 'video' ? 'video' : 'document', displayLabel);
            const fallbackPct = status === 'completed' ? 100 : (TRANSLATION_PHASE_PROGRESS[phase] ?? 30);
            const pct = normalizeTranslationProgress(progressArg, fallbackPct);
            barEl.style.width = pct + '%';
            if (videoModeActive) {
                setTranslationVideoProgress(displayLabel, pct, true);
            }
        }

        function stopTranslationPolling() {
            if (translationPollInterval) {
                clearInterval(translationPollInterval);
                translationPollInterval = null;
            }
            const container = document.getElementById('translationProgressContainer');
            if (container) container.style.setProperty('display', 'none', 'important');
        }

        async function translateReadme() {
            const status = document.getElementById('translationStatus');
            const languageSelect = document.getElementById('translationLanguage');
            const modeSelect = document.getElementById('translationMode');
            const input = document.getElementById('translationInput');
            const sourceVideoRadio = document.getElementById('translationSourceVideo');
            const isVideoMode = sourceVideoRadio && sourceVideoRadio.checked;
            const documentInput = document.getElementById('translationFile');
            const selectedDocumentFile = !isVideoMode
                ? ((documentInput && documentInput.files && documentInput.files[0]) || translationSelectedDocumentFile)
                : null;

            const targetLanguage = languageSelect ? languageSelect.value : 'en';
            const translationMode = modeSelect ? modeSelect.value : 'literal';
            const manualMarkdown = input ? input.value.trim() : '';
            const sourceMarkdown = manualMarkdown || translationOriginalMarkdown;

            if (isVideoMode) {
                const videoInput = document.getElementById('translationVideoFile');
                const file = videoInput && videoInput.files && videoInput.files[0];
                if (!file) {
                    alert('Выберите видеофайл для перевода субтитров.');
                    return;
                }
                if (!targetLanguage) {
                    alert('Выберите целевой язык перевода.');
                    return;
                }
            } else {
                if (!selectedDocumentFile && !sourceMarkdown) {
                    alert('Загрузите файл или вставьте текст для перевода.');
                    return;
                }
                if (!targetLanguage) {
                    alert('Выберите целевой язык перевода.');
                    return;
                }
            }

            stopTranslationPolling();
            resetTranslationUploadProgress();
            if (isVideoMode) {
                resetTranslationVideoResultPanel();
            }

            try {
                if (status) {
                    status.innerHTML = '<div class="info-box">Запуск перевода...</div>';
                }
                const progressContainer = document.getElementById('translationProgressContainer');
                const progressPhase = document.getElementById('translationProgressPhase');
                const progressBar = document.getElementById('translationProgressBar');
                if (progressContainer && progressPhase && progressBar) {
                    progressContainer.style.setProperty('display', isVideoMode ? 'none' : 'block', 'important');
                    progressPhase.textContent = isVideoMode ? 'Загрузка видео...' : (selectedDocumentFile ? 'Загрузка документа...' : 'Запуск...');
                    progressBar.style.width = '0%';
                }
                if (isVideoMode) {
                    setTranslationVideoProgress('Загрузка видео', 0, true);
                }

                let requestId;
                if (isVideoMode) {
                    const videoInput = document.getElementById('translationVideoFile');
                    const file = videoInput && videoInput.files && videoInput.files[0];
                    const outputModeSelect = document.getElementById('translationOutputMode');
                    const outputMode = (outputModeSelect && outputModeSelect.value) || 'both';
                    const subtitleStyle = 'boxed';
                    requestId = await startVideoTranslationUpload(file, targetLanguage, outputMode, subtitleStyle);
                } else if (selectedDocumentFile) {
                    requestId = await startDocumentTranslationUpload(selectedDocumentFile, targetLanguage, translationMode);
                } else {
                    const startResponse = await fetch(`${getTranslationApiUrl()}/translate/readme`, {
                        method: 'POST',
                        headers: {
                            ...getTranslationAuthHeaders(),
                            'Content-Type': 'application/json'
                        },
                        body: JSON.stringify({
                            markdown: sourceMarkdown,
                            target_language: targetLanguage,
                            llm_provider: window.getSelectedLlmProvider?.() || 'polza',
                            translation_mode: translationMode
                        })
                    });
                    if (!startResponse.ok) {
                        let detail = startResponse.statusText;
                        try {
                            const errJson = await startResponse.json();
                            detail = errJson.detail || JSON.stringify(errJson);
                        } catch {
                            detail = await startResponse.text().catch(() => startResponse.statusText);
                        }
                        throw new Error(detail || `Ошибка ${startResponse.status}`);
                    }
                    const startData = await startResponse.json();
                    requestId = startData.request_id;
                }

                if (!requestId) throw new Error('Нет request_id в ответе');
                translationCurrentRequestId = requestId;
                translationJobType = isVideoMode ? 'video' : 'document';
                if (isVideoMode) {
                    resetTranslationUploadProgress();
                }

                if (status) status.innerHTML = '<div class="info-box">' + (isVideoMode ? 'Обработка видео: распознавание и перевод' : 'Перевод выполняется. Это может занять несколько минут.') + '</div>';
                updateTranslationSummary(isVideoMode ? 'video' : 'document', 'В работе');
                updateTranslationProgress(isVideoMode ? 'queued' : 'translate', 'in_progress', isVideoMode ? 0 : undefined);

                translationPollInterval = setInterval(async () => {
                    try {
                        const statusResponse = await fetch(`${getTranslationApiUrl()}/translate/status/${requestId}`, { headers: getTranslationAuthHeaders() });
                        if (!statusResponse.ok) {
                            stopTranslationPolling();
                            let errMsg = statusResponse.status === 404
                                ? 'Задача перевода не найдена. Запустите перевод заново.'
                                : `Не удалось получить статус перевода: ${statusResponse.status}`;
                            try {
                                const errJson = await statusResponse.json();
                                if (errJson && errJson.detail) errMsg = errJson.detail;
                            } catch {
                                // Ignore parse errors and keep the status-based fallback.
                            }
                            updateTranslationSummary(isVideoMode ? 'video' : 'document', 'Ошибка обработки');
                            if (status) {
                                if (window.sanitize) {
                                    window.sanitize.safeSetErrorMessage(status, `Ошибка перевода: ${errMsg}`);
                                } else {
                                    // Safe fallback: textContent auto-escapes the server-provided message.
                                    status.textContent = `Ошибка перевода: ${errMsg}`;
                                }
                            }
                            if (window.toast) window.toast.error(errMsg);
                            return;
                        }
                        const job = await statusResponse.json();
                        const s = job.status;
                        const phase = job.phase || (isVideoMode ? 'extract_audio' : 'translate');
                        const progressPct = job.progress != null ? job.progress : undefined;
                        updateTranslationProgress(phase, s, progressPct);

                        if (s === 'completed') {
                            stopTranslationPolling();
                            const isVideoResult = job.job_type === 'video';
                            translationJobType = isVideoResult ? 'video' : 'document';
                            updateTranslationSummary(isVideoResult ? 'video' : 'document', 'Готово');
                            const hasDocxArtifact = !!(job.result_links && job.result_links.docx);
                            translationDocumentDownloadType = hasDocxArtifact ? 'docx' : 'markdown';
                            if (!isVideoResult && job.source_filename) {
                                translationFileName = String(job.source_filename).replace(/\.[^.]+$/, '') + (hasDocxArtifact ? '_translated.docx' : '_translated.md');
                            }
                            translationOriginalMarkdown = isVideoResult ? (job.original_transcript || '') : (job.original_markdown || sourceMarkdown);
                            translationTranslatedMarkdown = isVideoResult ? (job.translated_subtitles || '') : (job.translated_markdown || sourceMarkdown);
                            window.translationTranslatedMarkdown = translationTranslatedMarkdown;
                            const noResults = document.getElementById('translationNoResults');
                            const resultsArea = document.getElementById('translationResultsArea');
                            const mdBtn = document.getElementById('downloadTranslatedMarkdownBtn');
                            if (mdBtn) {
                                mdBtn.textContent = hasDocxArtifact ? '↓ Скачать DOCX' : '↓ Скачать';
                                mdBtn.onclick = hasDocxArtifact
                                    ? function() { downloadTranslationArtifact(translationCurrentRequestId, 'docx'); }
                                    : downloadTranslatedMarkdown;
                            }
                            if (isVideoResult) {
                                activateTranslationVideoScreen();
                                resetTranslationVideoResultPanel();
                                if (noResults) noResults.style.display = 'none';
                                if (resultsArea) resultsArea.style.display = 'none';
                                if (mdBtn) mdBtn.style.display = 'none';
                                setTranslationVideoProgress('Готово', 100, true);
                                showTranslationVideoResultPanel(job);
                            } else if (translationRenderAsMarkdown) {
                                resetTranslationVideoResultPanel();
                                if (noResults && resultsArea) {
                                    noResults.style.display = 'none';
                                    resultsArea.style.display = 'block';
                                }
                                if (mdBtn) mdBtn.style.display = 'inline-block';
                                displayMarkdown(translationOriginalMarkdown, 'translationOriginalContent');
                                displayMarkdown(translationTranslatedMarkdown, 'translationTranslatedContent');
                                updateTranslationTextMeta('original', translationOriginalMarkdown);
                                updateTranslationTextMeta('translated', translationTranslatedMarkdown);
                            } else {
                                resetTranslationVideoResultPanel();
                                if (noResults && resultsArea) {
                                    noResults.style.display = 'none';
                                    resultsArea.style.display = 'block';
                                }
                                if (mdBtn) mdBtn.style.display = 'inline-block';
                                displayPlainText(translationOriginalMarkdown, 'translationOriginalContent');
                                displayPlainText(translationTranslatedMarkdown, 'translationTranslatedContent');
                                updateTranslationTextMeta('original', translationOriginalMarkdown);
                                updateTranslationTextMeta('translated', translationTranslatedMarkdown);
                            }
                            if (status) {
                                if (window.sanitize) {
                                    window.sanitize.safeSetHTML(status, '<div class="success-msg">' + (isVideoResult ? 'Субтитры готовы' : 'Перевод завершён') + '</div>');
                                } else {
                                    // Safe fallback: plain text avoids any unsanitized HTML injection.
                                    status.textContent = isVideoResult ? 'Субтитры готовы' : 'Перевод завершён';
                                }
                            }
                            if (window.toast) window.toast.success(isVideoResult ? 'Субтитры успешно сгенерированы' : 'Перевод успешно выполнен');
                            return;
                        }

                        if (s === 'failed') {
                            stopTranslationPolling();
                            const errMsg = job.error || 'Неизвестная ошибка';
                            if (isVideoMode || job.job_type === 'video') {
                                setTranslationVideoProgress('Ошибка обработки', progressPct || 0, true);
                            }
                            if (status) {
                                if (window.sanitize) {
                                    window.sanitize.safeSetErrorMessage(status, `Ошибка перевода: ${errMsg}`);
                                } else {
                                    // Safe fallback: textContent auto-escapes the server-provided message.
                                    status.textContent = `Ошибка перевода: ${errMsg}`;
                                }
                            }
                            if (window.toast) window.toast.error(errMsg);
                            else alert('Ошибка перевода: ' + errMsg);
                        }
                    } catch (e) {
                        console.error('Ошибка опроса статуса перевода:', e);
                    }
                }, 2000);
            } catch (error) {
                console.error('Ошибка при переводе README:', error);
                stopTranslationPolling();
                if (isVideoMode) {
                    setTranslationVideoProgress('Ошибка загрузки', 0, true);
                }
                if (status) {
                    if (window.sanitize) {
                        window.sanitize.safeSetErrorMessage(status, `Ошибка перевода: ${error.message}`);
                    } else {
                        // Safe fallback: textContent auto-escapes the exception message.
                        status.textContent = `Ошибка перевода: ${error.message}`;
                    }
                }
                if (window.toast) {
                    window.toast.error(`Ошибка перевода: ${error.message}`);
                } else {
                    alert('Ошибка перевода: ' + error.message);
                }
            }
        }

        function downloadTranslatedMarkdown() {
            if (translationDocumentDownloadType === 'docx' && translationCurrentRequestId) {
                downloadTranslationArtifact(translationCurrentRequestId, 'docx');
                return;
            }
            if (!translationTranslatedMarkdown) {
                alert('Нет переведённого текста для скачивания. Сначала выполните перевод.');
                return;
            }

            const blob = new Blob([translationTranslatedMarkdown], { type: 'text/markdown;charset=utf-8' });
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = translationFileName || 'README_translated.md';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
        }

        async function downloadTranslatedSubtitles() {
            if (!translationCurrentRequestId) {
                alert('Нет готовых субтитров для скачивания. Сначала выполните перевод видео.');
                return;
            }
            try {
                const response = await fetch(`${getTranslationApiUrl()}/translate/subtitles/${translationCurrentRequestId}`, {
                    headers: getTranslationAuthHeaders()
                });
                if (!response.ok) throw new Error(response.statusText || 'Ошибка загрузки');
                const blob = await response.blob();
                const lang = document.getElementById('translationLanguage') && document.getElementById('translationLanguage').value || 'ru';
                const ext = (document.getElementById('translationSubtitleFormat') && document.getElementById('translationSubtitleFormat').value) || 'srt';
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `subtitles_${lang}.${ext}`;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                window.URL.revokeObjectURL(url);
            } catch (e) {
                console.error(e);
                alert('Не удалось скачать субтитры: ' + e.message);
            }
        }

        async function startDocumentTranslationUpload(file, targetLanguage, translationMode) {
            if (!file) {
                throw new Error('Файл документа не выбран');
            }

            const formData = new FormData();
            formData.append('file', file);
            formData.append('target_language', targetLanguage);
            formData.append('translation_mode', translationMode || 'literal');
            formData.append('llm_provider', window.getSelectedLlmProvider?.() || 'polza');

            const headers = { ...(getTranslationAuthHeaders() || {}) };
            Object.keys(headers).forEach((key) => {
                if (key.toLowerCase() === 'content-type') {
                    delete headers[key];
                }
            });

            const response = await fetch(`${getTranslationApiUrl()}/translate/document`, {
                method: 'POST',
                headers,
                body: formData
            });

            if (!response.ok) {
                let detail = response.statusText;
                try {
                    const errJson = await response.json();
                    detail = errJson.detail || JSON.stringify(errJson);
                } catch {
                    detail = await response.text().catch(() => response.statusText);
                }
                throw new Error(detail || `Ошибка ${response.status}`);
            }

            const data = await response.json();
            if (!data || !data.request_id) {
                throw new Error('Нет request_id в ответе сервера');
            }
            return data.request_id;
        }

        async function startVideoTranslationUpload(file, targetLanguage, outputMode, subtitleStyle) {
            return new Promise((resolve, reject) => {
                if (!file) {
                    reject(new Error('Файл видео не выбран'));
                    return;
                }
                const xhr = new XMLHttpRequest();
                xhr.open('POST', `${getTranslationApiUrl()}/translate/video`);

                const headers = getTranslationAuthHeaders() || {};
                Object.keys(headers).forEach((key) => {
                    if (key.toLowerCase() === 'content-type') return;
                    xhr.setRequestHeader(key, headers[key]);
                });

                xhr.upload.onprogress = function (event) {
                    if (!event.lengthComputable) return;
                    const percent = (event.loaded / event.total) * 100;
                    updateTranslationUploadProgress(percent);
                };

                xhr.onerror = function () {
                    reject(new Error('Ошибка сети при загрузке видео'));
                };

                xhr.onload = function () {
                    if (xhr.status < 200 || xhr.status >= 300) {
                        let detail = xhr.statusText || 'Ошибка ' + xhr.status;
                        try {
                            const errJson = JSON.parse(xhr.responseText || '{}');
                            if (errJson && errJson.detail) detail = errJson.detail;
                        } catch {
                            // ignore JSON parse error
                        }
                        reject(new Error(detail));
                        return;
                    }
                    try {
                        const data = JSON.parse(xhr.responseText || '{}');
                        if (!data || !data.request_id) {
                            reject(new Error('Нет request_id в ответе сервера'));
                        } else {
                            updateTranslationUploadProgress(100);
                            resolve(data.request_id);
                        }
                    } catch (e) {
                        reject(new Error('Ошибка разбора ответа сервера'));
                    }
                };

                const formData = new FormData();
                formData.append('file', file);
                formData.append('target_language', targetLanguage);
                formData.append('output_mode', outputMode);
                formData.append('subtitle_style', subtitleStyle);
                formData.append('llm_provider', window.getSelectedLlmProvider?.() || 'polza');

                xhr.send(formData);
            });
        }

        async function downloadTranslationArtifact(requestId, type) {
            if (!requestId || !type) return;
            try {
                const response = await fetch(`${getTranslationApiUrl()}/translate/download/${requestId}?type=${encodeURIComponent(type)}`, {
                    headers: getTranslationAuthHeaders()
                });
                if (!response.ok) throw new Error(response.statusText || 'Ошибка загрузки');
                const blob = await response.blob();
                const disp = response.headers.get('Content-Disposition');
                let filename = type + (type === 'video' ? '.mp4' : type === 'transcript' ? '_ru.json' : '.');
                if (disp) {
                    const m = disp.match(/filename="?([^";\n]+)"?/);
                    if (m) filename = m[1].trim();
                }
                if (filename.indexOf('.') < 0 && type === 'vtt') filename = 'subtitles.vtt';
                if (filename.indexOf('.') < 0 && type === 'srt') filename = 'subtitles.srt';
                if (filename.indexOf('.') < 0 && type === 'ass') filename = 'subtitles.ass';
                if (filename.indexOf('.') < 0 && type === 'docx') filename = translationFileName || 'document_translated.docx';
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = filename;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                window.URL.revokeObjectURL(url);
            } catch (e) {
                console.error(e);
                alert('Не удалось скачать файл: ' + e.message);
            }
        }

        if (typeof window !== 'undefined') {
            Object.assign(window, {
                handleTranslationFileSelect,
                translateReadme,
                downloadTranslatedMarkdown,
                downloadTranslatedSubtitles,
                resetTranslationState,
                resetTranslationUploadProgress,
                resetTranslationVideoProgress,
                resetTranslationVideoResultPanel,
                setTranslationVideoProgress,
                downloadTranslationArtifact,
                startDocumentTranslationUpload,
                startVideoTranslationUpload,
                updateTranslationSummary,
                syncTranslationLanguageBadges,
            });
        }



