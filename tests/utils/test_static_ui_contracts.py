import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "src" / "content_factory"


def test_auditor_original_ui_links_are_rewritten_to_shared_app_prefix():
    from content_factory.api.routers.auditor import _rewrite_auditor_page_html

    original_html = """
    <form method="post" action="/run"></form>
    <a href="/download?file=report.xlsx">XLSX</a>
    <a href="/logout">Выйти</a>
    <img src="/assets/avatar-placeholder.jpg" alt="">
    """

    rewritten = _rewrite_auditor_page_html(original_html)

    assert 'action="/app/auditor/run"' in rewritten
    assert 'href="/app/auditor/download?file=report.xlsx"' in rewritten
    assert 'href="/app/auditor/logout"' in rewritten
    assert 'src="/static/assets/avatar-placeholder.jpg"' in rewritten


def test_dashboard_mode_cards_use_direct_links_instead_of_js_only_navigation():
    html = (ROOT / "static" / "app.html").read_text(encoding="utf-8")

    expected_targets = [
        "/app/generate",
        "/app/auditor",
        "/app/translate",
        "/app/curriculum",
        "/app/spravochnik",
    ]
    for target in expected_targets:
        assert re.search(rf'<a class="dashboard-primary-action" href="{re.escape(target)}"', html)

    assert 'class="dashboard-primary-action" onclick=' not in html


def test_native_up_router_mirrors_curriculum_after_mutations():
    """Phase 5.5 cutover: the WSGI PrefixRewriteASGI mount is gone; the native up
    router now carries the "mirror UP data after a successful POST" side effect via
    ``_redirect_synced`` (best-effort ``sync_spravochnik_curriculum_plans``)."""

    import inspect

    from content_factory.catalog.web.routers import up

    # the sync helper exists and is invoked from the redirect-after-mutation path
    assert hasattr(up, "_sync_up_curriculum")
    assert hasattr(up, "_redirect_synced")

    src = inspect.getsource(up)
    # every UP-mutating POST redirects through the syncing variant, not the plain one
    for handler in (
        "up_cleanup_empty",
        "up_plan_delete",
        "up_plan_proposals_generate",
        "up_plan_proposal_post",
        "up_plan_row_new",
        "up_plan_row_post",
        "up_plan_row_delete",
    ):
        assert f"def {handler}" in src
    assert "_redirect_synced" in src
    # read-only GET detail must NOT sync
    detail_src = inspect.getsource(up.up_plan_detail)
    assert "_redirect_synced" not in detail_src


def test_markdown_preview_loads_heavy_vendors_lazily_from_local_static():
    html = (ROOT / "static" / "checker.html").read_text(encoding="utf-8")
    renderer = (ROOT / "static" / "js" / "modules" / "markdownRendering.js").read_text(encoding="utf-8")

    assert "/static/vendor/mermaid/mermaid.min.js" not in html
    assert "/static/vendor/marked/marked.min.js" not in html
    assert "/static/vendor/mathjax/tex-mml-chtml.js" not in html
    assert "/static/vendor/mermaid/mermaid.min.js" in renderer
    assert "/static/vendor/marked/marked.min.js" in renderer
    assert "/static/vendor/mathjax/tex-mml-chtml.js" in renderer
    assert "function ensureMermaidLoaded" in renderer
    assert "await ensureMarkedLoaded()" in renderer


def test_app_pages_enable_safe_navigation_prefetch():
    prefetch_js = (ROOT / "static" / "js" / "utils" / "pagePrefetch.js").read_text(encoding="utf-8")
    for page_name in ["app.html", "index.html", "auditor.html", "checker.html", "translator.html", "instruction.html"]:
        html = (ROOT / "static" / page_name).read_text(encoding="utf-8")
        assert "/static/js/utils/pagePrefetch.js?v=20260518-page-prefetch" in html
        assert "defer" in html

    assert "requestIdleCallback" in prefetch_js
    assert "pointerenter" in prefetch_js
    assert "X-ContentGen-Prefetch" in prefetch_js
    assert "cache: options.priority ? 'reload' : 'force-cache'" in prefetch_js


def test_protected_pages_use_central_auth_session_guard():
    auth_js = (ROOT / "static" / "js" / "utils" / "authSession.js").read_text(encoding="utf-8")
    for page_name in ["app.html", "index.html", "auditor.html", "checker.html", "translator.html", "instruction.html"]:
        html = (ROOT / "static" / page_name).read_text(encoding="utf-8")
        assert "/static/js/utils/authSession.js?v=20260702-auth-cookie-sync" in html

    assert "window.fetch = authFetch" in auth_js
    assert "/auth/me" in auth_js
    assert "/auth/session-cookie" in auth_js
    assert "ensureNavigationCookie" in auth_js
    assert "await ensureNavigationCookie();\n                return true;" in auth_js
    assert "'/app/auditor'" in auth_js
    assert "'/app/curriculum'" in auth_js
    assert "'/app/spravochnik'" in auth_js
    assert "response.status === 401" in auth_js
    assert "clearAuthState" in auth_js
    assert "window.location.replace('/')" in auth_js


def test_auth_router_exposes_navigation_cookie_sync_routes():
    from fastapi.routing import APIRoute

    from content_factory.api.routers.auth import router

    routes = {
        (route.path, method)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods
    }

    assert ("/me", "GET") in routes
    assert ("/auth/me", "GET") in routes
    assert ("/session-cookie", "POST") in routes
    assert ("/auth/session-cookie", "POST") in routes


def test_markdown_renderer_cache_version_matches_diagram_fit():
    for page_name in ["index.html", "checker.html", "translator.html"]:
        html = (ROOT / "static" / page_name).read_text(encoding="utf-8")
        assert "/static/js/modules/markdownRendering.js?v=20260520-methodology-diagram-stability" in html


def test_upload_cards_do_not_reopen_file_picker_from_bubbled_input_clicks():
    generator_html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    checker_html = (ROOT / "static" / "checker.html").read_text(encoding="utf-8")
    translator_html = (ROOT / "static" / "translator.html").read_text(encoding="utf-8")

    for input_id, html in [
        ("curriculumFile", generator_html),
        ("readmeFile", checker_html),
        ("checkerCurriculumFile", checker_html),
        ("translationFile", translator_html),
        ("translationVideoFile", translator_html),
    ]:
        assert f"if (event.target.tagName !== 'INPUT') document.getElementById('{input_id}').click()" in html
        assert f'id="{input_id}"' in html
        assert 'onclick="event.stopPropagation()"' in html


def test_translator_target_language_badge_tracks_selected_language():
    html = (ROOT / "static" / "translator.html").read_text(encoding="utf-8")
    js = (ROOT / "static" / "js" / "modules" / "translationPage.js").read_text(encoding="utf-8")

    assert 'id="translationTargetLanguageBadge"' in html
    assert "/static/js/modules/translationPage.js?v=" in html
    assert "function syncTranslationLanguageBadges" in js
    assert "setTranslationText('translationTargetLanguageBadge', `✓ ${language} · ПЕРЕВОД`)" in js
    assert "setTranslationText('translationSummaryLanguage', `RU → ${language}`)" in js
    assert "targetBadge.textContent = `✓ ${language} · ПЕРЕВОД`" in html
    assert "summaryLanguage.textContent = `RU → ${language}`" in html
    assert "ПЕРЕВОД ДОКУМЕНТА · RU → EN';" not in js


def test_translator_polling_reports_missing_translation_job():
    js = (ROOT / "static" / "js" / "modules" / "translationPage.js").read_text(encoding="utf-8")

    assert "if (!statusResponse.ok)" in js
    assert "Задача перевода не найдена. Запустите перевод заново." in js
    assert "updateTranslationSummary(isVideoMode ? 'video' : 'document', 'Ошибка обработки')" in js


def test_dashboard_parses_backend_timestamps_as_utc_and_blocks_terminal_runs():
    html = (ROOT / "static" / "app.html").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "s21-dashboard.css").read_text(encoding="utf-8")

    assert "function parseApiDate" in html
    assert "raw.replace(' ', 'T') + 'Z'" in html
    assert "formatRunTime(data.last_run_at)" in html
    assert "function dashboardStatusLabel" in html
    assert "interrupted: 'ПРЕРВАНО'" in html
    assert "function isRecentRunOpenable" in html
    assert "['failed', 'cancelled'].includes(status)" in html
    assert "function isRecentRunActive" in html
    assert "function saveActiveGenerationForRestore" in html
    assert "function resumeInterruptedRun" in html
    assert "function cancelRecentRun" in html
    assert "/generate/workflow/${requestId}/command" in html
    assert "/generate/cancel/${requestId}" in html
    assert "Остановить запуск" in html
    assert "Нет результата" in html
    assert ".dashboard-run-actions" in css
    assert ".dashboard-run-cancel" in css
    assert ".dashboard-run-open:disabled" in css


def test_translator_video_limit_hint_matches_backend_default():
    html = (ROOT / "static" / "translator.html").read_text(encoding="utf-8")

    assert "До 500 MB" in html
    assert "До 100 MB" not in html


def test_instruction_tab_is_user_facing_and_uses_product_style():
    main_py = (PKG / "api" / "main.py").read_text(encoding="utf-8")
    instruction_html = (ROOT / "static" / "instruction.html").read_text(encoding="utf-8")
    instruction_css = (ROOT / "static" / "css" / "s21-instruction.css").read_text(encoding="utf-8")
    nav_pages = [
        ROOT / "static" / "app.html",
        ROOT / "static" / "index.html",
        ROOT / "static" / "checker.html",
        ROOT / "static" / "translator.html",
        ROOT / "static" / "instruction.html",
    ]

    assert '@app.get("/app/instruction")' in main_py
    assert 'instruction_path = static_path / "instruction.html"' in main_py
    assert "s21-instruction.css" in instruction_html
    assert '<body class="s21-product page-instruction">' in instruction_html
    assert '<a href="/app/instruction" class="active">Инструкция</a>' in instruction_html
    assert "Как работать с генератором" in instruction_html
    assert "Что означают параметры" in instruction_html
    assert "Методологический режим" in instruction_html
    assert "Сторителлинг / SJM" in instruction_html
    assert "Как писать правки" in instruction_html
    assert "Финальный результат" in instruction_html
    assert "Аудитор" in instruction_html
    assert "Переводчик" in instruction_html
    assert "Для таджикского и кыргызского ожидается кириллица" in instruction_html
    assert "Для узбекского ожидается латиница" in instruction_html
    assert "background: var(--s21-bg);" in instruction_css
    assert "background: var(--s21-surface);" in instruction_css
    assert "border: 1px solid var(--s21-border);" in instruction_css
    assert "color: var(--s21-ink);" in instruction_css
    assert "color: var(--s21-ink-2);" in instruction_css
    assert "border-radius: var(--s21-radius-lg);" in instruction_css
    assert "@media (max-width: 980px)" in instruction_css
    expected_nav = ["Главная", "Генерация", "Аудитор", "Перевод", "УП", "Справочник", "Инструкция"]
    for page in nav_pages:
        page_html = page.read_text(encoding="utf-8")
        assert "Инструкция" in page_html
        nav_match = re.search(r'<nav class="dashboard-nav"[^>]*>(.*?)</nav>', page_html, re.S)
        assert nav_match is not None
        nav_labels = [
            re.sub(r"<[^>]+>", "", item).strip()
            for item in re.findall(r"<a[^>]*>(.*?)</a>", nav_match.group(1), re.S)
        ]
        assert nav_labels == expected_nav
    auditor_html = (ROOT / "static" / "auditor.html").read_text(encoding="utf-8")
    auditor_topbar = re.search(r'<header class="topbar">(.*?)</header>', auditor_html, re.S)
    assert auditor_topbar is not None
    assert "Аудит контента" in auditor_topbar.group(1)
    assert "проверка учебных проектов" in auditor_topbar.group(1)
    assert "Сводка" in auditor_topbar.group(1)
    assert "Выйти" in auditor_topbar.group(1)
    assert 'class="dashboard-nav"' not in auditor_html
    assert "Проверка локального проекта" in auditor_html
    forbidden_copy = ["архитектур", "внутренн", "код", "endpoint", "service", "schema", "pipeline"]
    lower_instruction = instruction_html.lower()
    assert all(term not in lower_instruction for term in forbidden_copy)


def test_translator_video_progress_is_runtime_driven():
    html = (ROOT / "static" / "translator.html").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "s21-translation.css").read_text(encoding="utf-8")
    js = (ROOT / "static" / "js" / "modules" / "translationPage.js").read_text(encoding="utf-8")

    assert 'id="translationVideoProgressBar"' in html
    assert "72 %" not in html
    assert "Распознавание речи" not in html
    assert "var(--s21-accent) 72%" not in css
    assert "setTranslationVideoProgress(displayLabel, pct, true)" in js
    assert "const progressPct = job.progress != null ? job.progress : undefined;" in js


def test_translator_video_downloads_render_inside_video_tab():
    html = (ROOT / "static" / "translator.html").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "s21-translation.css").read_text(encoding="utf-8")
    js = (ROOT / "static" / "js" / "modules" / "translationPage.js").read_text(encoding="utf-8")

    assert 'id="translationVideoResultPanel"' in html
    assert 'id="translationVideoInlineDownloadLinks"' in html
    assert 'id="translationVideoDownloadSection"' not in html
    assert 'id="translationVideoDownloadLinks"' not in html
    assert 'id="downloadTranslatedSubtitlesBtn"' not in html
    assert "Скачайте нужные файлы здесь" in html
    assert ".translate-video-result-panel" in css
    assert ".translate-video-download-links" in css
    assert "function renderTranslationVideoDownloadButtons" in js
    assert "function showTranslationVideoResultPanel" in js
    assert "function activateTranslationVideoScreen" in js
    assert "resetTranslationVideoResultPanel();" in js
    assert "showTranslationVideoResultPanel(job);" in js
    assert "if (resultsArea) resultsArea.style.display = 'none';" in js
    assert "renderTranslationVideoDownloadButtons(links, resultLinks);" in js
    assert "downloadTranslationArtifact(translationCurrentRequestId, type)" in js


def test_translator_video_tts_placeholder_is_removed():
    html = (ROOT / "static" / "translator.html").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "s21-translation.css").read_text(encoding="utf-8")

    assert "Сохранить голос диктора" not in html
    assert "Клонировать тембр оригинала" not in html
    assert "TTS" not in html
    assert "translate-tts-box" not in html
    assert "translate-switch" not in css


def test_translator_document_empty_state_copy():
    html = (ROOT / "static" / "translator.html").read_text(encoding="utf-8")
    js = (ROOT / "static" / "js" / "modules" / "translationPage.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "s21-translation.css").read_text(encoding="utf-8")

    assert "Результат перевода пишется латиницей" not in html
    assert "Загрузите README или документ" in html
    assert "Загрузите README или Markdown" not in html
    assert "Документ для перевода" in html
    assert "Markdown-файл для перевода" not in html
    assert "Документ для перевода" in js
    assert ".translate-empty-state p" in css
    assert ".translate-compare-card" in css
    assert "max-width: none;" in css


def test_translator_accepts_common_document_upload_formats():
    html = (ROOT / "static" / "translator.html").read_text(encoding="utf-8")
    js = (ROOT / "static" / "js" / "modules" / "translationPage.js").read_text(encoding="utf-8")
    router = (PKG / "api" / "routers" / "readme_translate.py").read_text(encoding="utf-8")
    doc_service = (PKG / "api" / "routers" / "document_translation.py").read_text(encoding="utf-8")

    assert 'accept=".md,.markdown,.txt,.html,.htm,.docx,.pdf"' in html
    assert "TXT, Markdown, HTML, DOCX, PDF" in html
    assert "Перевод документов" in html
    assert "Результат: <strong>Markdown</strong> · <strong>текст документа</strong>" in html
    assert "startDocumentTranslationUpload" in js
    assert "/translate/document" in js
    assert "TRANSLATION_CLIENT_READABLE_EXTENSIONS" in js
    assert "@router.post(\"/translate/document\"" in router
    # The document extraction service (extensions constant) was extracted out of the router.
    assert "TRANSLATION_DOCUMENT_EXTENSIONS = {\".md\", \".markdown\", \".txt\", \".html\", \".htm\", \".docx\", \".pdf\"}" in doc_service


def test_checker_heading_uses_generic_readme_copy():
    html = (ROOT / "static" / "checker.html").read_text(encoding="utf-8")

    assert "Проверка README" in html
    assert "Проверка собственного README" not in html


def test_checker_result_controls_are_not_duplicated_and_use_green_score_ring():
    html = (ROOT / "static" / "checker.html").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "s21-checker.css").read_text(encoding="utf-8")

    assert html.count("Очистить результат") == 1
    assert 'id="clearResultsBtn"' not in html
    assert "/static/css/s21-checker.css?v=20260518-diagram-polish" in html
    assert "--score-color: var(--s21-success);" in css
    assert "#b06d10" not in css


def test_metrics_filters_keep_state_without_generation_runtime():
    checker_html = (ROOT / "static" / "checker.html").read_text(encoding="utf-8")
    generator_html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "static" / "js" / "modules" / "metricsView.js").read_text(encoding="utf-8")

    assert "/static/js/modules/metricsView.js?v=20260518-checker-filter-state" in checker_html
    assert "/static/js/modules/metricsView.js?v=20260518-checker-filter-state" in generator_html
    assert "let activeMetricFilter = 'all';" in js
    assert "window.currentMetricFilter || activeMetricFilter || 'all'" in js
    assert "activeMetricFilter = filter;" in js
    assert "window.currentMetricFilter = filter;" in js


def test_main_exports_shared_markdown_renderer_for_checker():
    js = (ROOT / "static" / "js" / "main.js").read_text(encoding="utf-8")

    assert "window.displayMarkdown = displayMarkdown;" in js
    assert "window.renderMarkdownPreview = renderMarkdownPreview;" in js
    assert "window.normalizeMarkdownForDisplay = normalizeMarkdownForDisplay;" in js
    assert "window.renderMermaidDiagrams = renderMermaidDiagrams;" in js


def test_checker_readme_preview_uses_checker_diagram_fit_context():
    html = (ROOT / "static" / "checker.html").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "s21-checker.css").read_text(encoding="utf-8")
    renderer = (ROOT / "static" / "js" / "modules" / "markdownRendering.js").read_text(encoding="utf-8")
    preview_js = (ROOT / "static" / "js" / "modules" / "checkerReadmePreview.js").read_text(encoding="utf-8")

    assert "/static/js/modules/checkerReadmePreview.js?v=20260518-checker-diagram-fit" in html
    assert "function markdownRenderOptionsForContainer" in renderer
    assert "diagramContext: 'checker'" in renderer
    assert "const isChecker = renderContext === 'checker';" in renderer
    assert "baseWidth: 680" in renderer
    assert "boxWidth: 860" in renderer
    assert "maxEstimatedHeight: 520" in renderer
    assert ".result-markdown.markdown-preview figure.diagram-figure" in css
    assert "max-width: min(900px, 100%) !important;" in css
    assert "max-width: min(860px, 100%) !important;" in css
    assert "max-height: 540px !important;" in css
    assert 'id="improvedReadmePreview" class="markdown-preview result-markdown"' in preview_js


def test_generator_results_tabs_have_data_tab_without_methodologist_tab():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    assert "showTab('generatedData'" in html
    assert 'id="generatedDataContent"' in html
    assert "showTab('methodology'" not in html


def test_generator_exposes_storytelling_type_before_generation():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    form_state_js = (ROOT / "static" / "js" / "modules" / "generationFormState.js").read_text(encoding="utf-8")
    curriculum_js = (ROOT / "static" / "js" / "modules" / "curriculumForm.js").read_text(encoding="utf-8")
    main_js = (ROOT / "static" / "js" / "main.js").read_text(encoding="utf-8")
    generate_css = (ROOT / "static" / "css" / "s21-generate.css").read_text(encoding="utf-8")

    assert 'id="storytellingType"' in html
    assert "Тип сторителлинга" in html
    assert "Практический сторителлинг / SJM" in html
    assert 'id="storytellingTypeHelpTrigger"' in html
    assert 'id="storytellingTypeHelp"' not in html
    assert "storytelling-help-popover" not in html
    assert "storytelling_type: getInputValue('storytellingType') || 'sjm'" in form_state_js
    assert "setInputValue('storytellingType', seed.storytelling_type)" in form_state_js
    assert "STORYTELLING_TYPE_HELP" in form_state_js
    assert "FIELD_HELP_TEXT" in form_state_js
    assert "initializeGeneratorFieldHelp" in form_state_js
    assert "initializeStorytellingTypeHelp" in form_state_js
    assert "updateStorytellingTypeHelp" in form_state_js
    assert "Пояснение поля:" in form_state_js
    assert "projectDescription: 'Кратко опишите суть проекта" in form_state_js
    assert "setCurriculumFieldValue('storytellingType', project.storytelling_type || 'sjm')" in curriculum_js
    assert "setValue('storytellingType', 'sjm')" in main_js
    assert ".storytelling-help-popover" not in generate_css
    assert ".field-help-popover" in generate_css
    assert ".storytelling-help-trigger:hover" in generate_css
    assert "background: #def7ec !important;" in generate_css
    assert "background: var(--s21-accent) !important;" in generate_css
    assert "color: var(--s21-dark) !important;" in generate_css


def test_methodology_review_actions_live_in_assistant_chat():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    panel_js = (ROOT / "static" / "js" / "modules" / "methodologyPanel.js").read_text(encoding="utf-8")
    chat_js = (ROOT / "static" / "js" / "modules" / "methodologyAssistantChat.js").read_text(encoding="utf-8")
    methodology_css = (ROOT / "static" / "css" / "s21-methodology.css").read_text(encoding="utf-8")

    assert 'id="assistantChatActions"' in html
    assert 'id="assistantActionContinue"' in html
    assert 'id="assistantActionEdit"' in html
    assert 'id="assistantActionAccept"' in html
    assert 'id="assistantActionCompare"' in html
    assert 'id="assistantTargetPicker"' in html
    assert 'id="assistantTargetChips"' in html
    assert 'id="assistantEditDraft"' not in html
    assert "methodology-review-toolbar" not in panel_js
    assert "methodologyAssistantInput" not in panel_js
    assert "runReviewAction" in chat_js
    assert "toggleTargetPicker" in chat_js
    assert "submitDraftChange" not in chat_js
    assert "function diffChangeSummary" in panel_js
    assert "methodology-diff-summary" in panel_js
    assert "methodology-readme-fragment-section" in panel_js
    assert "methodology-generated-details" in panel_js
    assert "window.MethodologyAssistantChat?.show?.(config.getState?.().currentGenerationStatus || 'in_progress')" in panel_js
    assert "body.s21-product .methodology-history-details .methodology-stage-item" in methodology_css
    assert "body.s21-product .methodology-diff-preview .diff-removed" in methodology_css
    assert "body.s21-product .methodology-readme-fragment-section" in methodology_css
    assert "body.s21-product .methodology-generated-details .methodology-generated-heading::before" in methodology_css


def test_methodology_panel_has_assistant_command_chat():
    js = (ROOT / "static" / "js" / "modules" / "methodologyAssistantChat.js").read_text(encoding="utf-8")

    assert "assistantChatInput" in js
    assert "submitCommand" in js
    assert "/assistant-command" in js
    assert "selected_target_id" in js


def test_main_extracts_table_captions_and_renders_generated_data_tab():
    renderer_js = (ROOT / "static" / "js" / "modules" / "markdownRendering.js").read_text(encoding="utf-8")
    result_tabs_js = (ROOT / "static" / "js" / "modules" / "generationResultTabs.js").read_text(encoding="utf-8")
    main_js = (ROOT / "static" / "js" / "main.js").read_text(encoding="utf-8")

    assert "function extractTableCaption" in renderer_js
    assert "function renderGeneratedDataTab" in result_tabs_js
    assert "renderGeneratedDataTab(data.result);" in main_js


def test_generation_results_return_to_readme_tab_after_methodology_completion():
    js = (ROOT / "static" / "js" / "modules" / "generationResultTabs.js").read_text(encoding="utf-8")
    main_js = (ROOT / "static" / "js" / "main.js").read_text(encoding="utf-8")

    assert "function activateResultTab" in js
    assert "activateResultTab('readme');" in main_js


def test_generation_runtime_split_modules_are_loaded():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    main_js = (ROOT / "static" / "js" / "main.js").read_text(encoding="utf-8")
    polling_js = (ROOT / "static" / "js" / "modules" / "generationPolling.js").read_text(encoding="utf-8")
    persistence_js = (ROOT / "static" / "js" / "modules" / "generationPersistence.js").read_text(encoding="utf-8")

    assert "generationPolling.js" in html
    assert "generationPersistence.js" in html
    assert "async function pollGenerationStatus" in polling_js
    assert "async function loadGenerationState" in persistence_js
    assert "async function pollGenerationStatus" not in main_js
    assert "async function loadGenerationState" not in main_js


def test_generation_restore_keeps_seed_for_active_runs():
    persistence_js = (ROOT / "static" / "js" / "modules" / "generationPersistence.js").read_text(encoding="utf-8")

    assert "function extractProjectSeedFromStatusData" in persistence_js
    assert "workflowMetadata.project_seed_payload" in persistence_js
    assert "function restoreSeedIntoForm" in persistence_js
    assert "restoreSeedIntoForm(restoredSeed);" in persistence_js
    assert "window.showGenerationRunView?.(latest.currentSeed || {}, {" in persistence_js


def test_methodology_assistant_chat_is_split_from_main():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    main_js = (ROOT / "static" / "js" / "main.js").read_text(encoding="utf-8")
    chat_js = (ROOT / "static" / "js" / "modules" / "methodologyAssistantChat.js").read_text(encoding="utf-8")

    assert "methodologyAssistantChat.js" in html
    assert "window.MethodologyAssistantChat" in chat_js
    assert "async function send" in chat_js
    assert "assistant-command" in chat_js
    assert "refreshReviewControls" in chat_js
    assert "function initializeMethodologyAssistantChat" not in main_js
    assert "async function sendAssistantChatMessage" not in main_js
    assert "window.MethodologyAssistantChat?.configure" in main_js


def test_methodology_assistant_chat_is_mode_bound_and_windowed():
    main_js = (ROOT / "static" / "js" / "main.js").read_text(encoding="utf-8")
    chat_js = (ROOT / "static" / "js" / "modules" / "methodologyAssistantChat.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "s21-generate.css").read_text(encoding="utf-8")

    assert "isEnabled: () =>" in main_js
    assert "getWorkflowCapability('methodology_assistant')" in main_js
    assert "workflowCapabilities" in chat_js
    assert "function isMethodologyMode" in chat_js
    assert "methodologyHumanReview" in chat_js
    assert "function makeChatDraggable" in chat_js
    assert "function normalizeChatBounds" in chat_js
    assert "window.addEventListener('resize'" in chat_js
    assert "methodology_assistant_chat_bounds_v2" in chat_js
    assert ".assistant-chat-actions" in css
    assert ".assistant-target-picker" in css
    assert ".assistant-target-chip.is-active" in css
    assert ".assistant-chat-input-row textarea:focus-visible" in css
    assert "background: var(--s21-surface-2);" in css
    assert "border-radius: var(--s21-radius-sm);" in css
    assert "resize: both;" in css
    assert "cursor: move;" in css
    assert "max-height: calc(100vh - 24px);" in css
    assert "height: min(720px, calc(100vh - 96px));" in css


def test_generation_pipeline_ui_matches_runtime_flow_without_antiplag_stage():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    run_view_js = (ROOT / "static" / "js" / "modules" / "generationRunView.js").read_text(encoding="utf-8")
    flow_yaml = (PKG / "generation" / "config" / "flow.yaml").read_text(encoding="utf-8")

    assert "antiplag" not in flow_yaml.lower()
    assert "data-run-stage=\"antiplagiarism\"" not in html
    assert "Антиплагиат" not in html
    assert "id: 'antiplagiarism'" not in run_view_js
    assert "plagiarism: 'antiplagiarism'" not in run_view_js
    assert "data-run-stage=\"translation\"" not in html
    assert "generationRunTranslationHint" not in html
    assert "id: 'translation'" not in run_view_js
    assert "translate: 93" not in run_view_js
    assert "generationRunStageTotal\">8<" in html


def test_curriculum_form_runtime_is_split_from_main():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    main_js = (ROOT / "static" / "js" / "main.js").read_text(encoding="utf-8")
    curriculum_js = (ROOT / "static" / "js" / "modules" / "curriculumForm.js").read_text(encoding="utf-8")

    assert "curriculumForm.js" in html
    assert 'id="persistedCurriculumPlan"' in html
    assert "loadPersistedCurriculumPlan()" in html
    assert "async function handleCurriculumUpload" in curriculum_js
    assert "async function loadPersistedCurriculumPlans" in curriculum_js
    assert "async function loadPersistedCurriculumPlan" in curriculum_js
    assert "/curriculum/plans" in curriculum_js
    assert "function buildCurriculumContext" in curriculum_js
    assert "getCurrentCurriculumContext" in curriculum_js
    assert "async function handleCurriculumUpload" not in main_js
    assert "function buildCurriculumContext" not in main_js
    assert "getCurrentCurriculumContext" in main_js


def test_metrics_view_runtime_is_split_from_main():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    main_js = (ROOT / "static" / "js" / "main.js").read_text(encoding="utf-8")
    metrics_js = (ROOT / "static" / "js" / "modules" / "metricsView.js").read_text(encoding="utf-8")

    assert "metricsView.js" in html
    assert "function displayMetrics" in metrics_js
    assert "function displayReport" in metrics_js
    assert "function filterMetrics" in metrics_js
    assert "function displayMetrics" not in main_js
    assert "function displayReport" not in main_js
    assert "function filterMetrics" not in main_js


def test_checker_runtime_is_split_into_focused_modules():
    html = (ROOT / "static" / "checker.html").read_text(encoding="utf-8")
    checker_js = (ROOT / "static" / "js" / "modules" / "checkerPage.js").read_text(encoding="utf-8")
    preview_js = (ROOT / "static" / "js" / "modules" / "checkerReadmePreview.js").read_text(encoding="utf-8")
    diff_js = (ROOT / "static" / "js" / "modules" / "checkerDiffView.js").read_text(encoding="utf-8")
    metrics_js = (ROOT / "static" / "js" / "modules" / "checkerMetricsSwitch.js").read_text(encoding="utf-8")
    curriculum_js = (ROOT / "static" / "js" / "modules" / "checkerCurriculumState.js").read_text(encoding="utf-8")
    modal_js = (ROOT / "static" / "js" / "modules" / "checkerImprovementModal.js").read_text(encoding="utf-8")
    run_js = (ROOT / "static" / "js" / "modules" / "checkerImprovementRun.js").read_text(encoding="utf-8")

    assert "checkerReadmePreview.js" in html
    assert "checkerDiffView.js" in html
    assert "checkerMetricsSwitch.js" in html
    assert "checkerCurriculumState.js" in html
    assert "checkerImprovementModal.js" in html
    assert "checkerImprovementRun.js" in html
    assert "function displayImprovedReadme" in preview_js
    assert "function displayReadmeDiff" in diff_js
    assert "function switchCheckerMetricsVersion" in metrics_js
    assert "function handleCheckerCurriculumUpload" in curriculum_js
    assert "function startImprovement" in modal_js
    assert "function generateImprovedReadme" in run_js
    assert "function displayImprovedReadme" not in checker_js
    assert "function displayReadmeDiff" not in checker_js
    assert "function switchCheckerMetricsVersion" not in checker_js
    assert "function handleCheckerCurriculumUpload" not in checker_js
    assert "function startImprovement" not in checker_js
    assert "function generateImprovedReadme" not in checker_js


def test_checker_page_styles_are_split_from_design_monolith():
    html = (ROOT / "static" / "checker.html").read_text(encoding="utf-8")
    design_css = (ROOT / "static" / "css" / "s21-design.css").read_text(encoding="utf-8")
    checker_css = (ROOT / "static" / "css" / "s21-checker.css").read_text(encoding="utf-8")
    tokens_css = (ROOT / "static" / "css" / "s21-tokens.css").read_text(encoding="utf-8")
    base_css = (ROOT / "static" / "css" / "s21-base.css").read_text(encoding="utf-8")
    forms_css = (ROOT / "static" / "css" / "s21-forms.css").read_text(encoding="utf-8")
    buttons_css = (ROOT / "static" / "css" / "s21-buttons.css").read_text(encoding="utf-8")
    badges_css = (ROOT / "static" / "css" / "s21-badges.css").read_text(encoding="utf-8")
    markdown_css = (ROOT / "static" / "css" / "s21-markdown.css").read_text(encoding="utf-8")

    assert "s21-checker.css" in html
    assert "s21-tokens.css" in html
    assert "s21-base.css" in html
    assert "s21-forms.css" in html
    assert "s21-buttons.css" in html
    assert "s21-badges.css" in html
    assert "s21-markdown.css" in html
    assert "body.s21-product.page-checker" in checker_css
    assert "body.s21-product.page-checker" not in design_css
    assert "МОДАЛКА" not in checker_css
    assert ":root" in tokens_css
    assert "body.s21-product {" in base_css
    assert "body.s21-product input[type=\"text\"]" in forms_css
    assert "body.s21-product .btn" in buttons_css
    assert "body.s21-product .badge" in badges_css
    assert "body.s21-product .markdown-preview" in markdown_css


def test_page_specific_product_styles_are_split_from_design_monolith():
    index_html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    app_html = (ROOT / "static" / "app.html").read_text(encoding="utf-8")
    auditor_html = (ROOT / "static" / "auditor.html").read_text(encoding="utf-8")
    checker_html = (ROOT / "static" / "checker.html").read_text(encoding="utf-8")
    translator_html = (ROOT / "static" / "translator.html").read_text(encoding="utf-8")
    instruction_html = (ROOT / "static" / "instruction.html").read_text(encoding="utf-8")
    design_css = (ROOT / "static" / "css" / "s21-design.css").read_text(encoding="utf-8")
    workflow_css = (ROOT / "static" / "css" / "s21-workflow.css").read_text(encoding="utf-8")
    methodology_css = (ROOT / "static" / "css" / "s21-methodology.css").read_text(encoding="utf-8")
    generate_css = (ROOT / "static" / "css" / "s21-generate.css").read_text(encoding="utf-8")
    metrics_css = (ROOT / "static" / "css" / "s21-metrics.css").read_text(encoding="utf-8")
    dashboard_css = (ROOT / "static" / "css" / "s21-dashboard.css").read_text(encoding="utf-8")
    auditor_css = (ROOT / "static" / "css" / "s21-auditor.css").read_text(encoding="utf-8")
    translation_css = (ROOT / "static" / "css" / "s21-translation.css").read_text(encoding="utf-8")
    instruction_css = (ROOT / "static" / "css" / "s21-instruction.css").read_text(encoding="utf-8")

    assert "s21-workflow.css" in index_html
    assert "s21-methodology.css" in index_html
    assert "s21-generate.css" in index_html
    assert "s21-metrics.css" in index_html
    assert "s21-dashboard.css" in app_html
    assert "s21-auditor.css" in auditor_html
    assert "s21-instruction.css" in instruction_html
    assert "s21-workflow.css" in checker_html
    assert "s21-metrics.css" in checker_html
    assert "s21-workflow.css" in translator_html
    assert "body.s21-product .generation-status-panel {" in workflow_css
    assert "body.s21-product .methodology-review-workspace {" in methodology_css
    assert "body.s21-product.page-generate {" in generate_css
    assert ".s21-metrics-view {" in metrics_css
    assert "body.s21-product.page-menu {" in dashboard_css
    assert "body.s21-product.page-auditor {" in auditor_css
    assert ".translate-topbar {" in translation_css
    assert "body.s21-product.page-instruction {" in instruction_css
    assert "body.s21-product .generation-status-panel {" not in design_css
    assert "body.s21-product.page-generate {" not in design_css
    assert "body.s21-product.page-instruction {" not in design_css
    assert ".s21-metrics-view {" not in design_css
    assert "body.s21-product.page-menu {" not in design_css
    assert "body.s21-product.page-auditor {" not in design_css
    assert ".dashboard-header {" not in design_css
    assert ".generator-topbar {" not in design_css
    assert ".s21-metric-row {" not in design_css
    assert ".translate-topbar {" not in design_css
    assert ".methodology-assistant-chat {" not in design_css
    assert "@media (max-width: 820px)" in dashboard_css
    assert "@media (max-width: 820px)" in generate_css
    assert "@media (max-width: 820px)" in metrics_css
    assert "@media (max-width: 820px)" in translation_css
    assert "@media (max-width: 980px)" in instruction_css
    assert "@media (max-width: 980px)" in auditor_css


def test_methodology_review_renders_requirement_matrix_as_ui():
    js = (ROOT / "static" / "js" / "modules" / "methodologyPanel.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "styles.css").read_text(encoding="utf-8")
    methodology_css = (ROOT / "static" / "css" / "s21-methodology.css").read_text(encoding="utf-8")

    assert "function renderRequirementsMatrix" in js
    assert "function renderRubricCriteria" in js
    assert "function renderContextReview" in js
    assert "function renderPlanningReview" in js
    assert "key === 'requirements_matrix'" in js
    assert "requirementsMatrix = renderRequirementsMatrix(artifact.requirements_matrix)" in js
    assert "'requirements_matrix'].includes(key)" in js
    assert "key === 'rubric'" in js
    assert "key === 'context_review'" in js
    assert "key === 'planning_review'" in js
    assert "window.displayMetrics(rubric, node.id)" in js
    assert "methodology-requirements-matrix" in js
    assert "methodology-rubric-view" in js
    assert "methodology-rubric-details" in js
    assert "methodology-rubric-summary" in js
    assert "methodology-context-review" in js
    assert "methodology-planning-review" in js
    assert ".methodology-requirements-matrix" in css
    assert ".methodology-requirement-row.status-fail" in css
    assert ".methodology-context-details .methodology-requirements-matrix" in methodology_css
    assert ".methodology-rubric-details" in methodology_css
    assert ".methodology-rubric-summary" in methodology_css
    assert ".methodology-structure-row," in methodology_css
    assert "background: transparent !important;" in methodology_css


def test_methodology_review_expanded_panels_use_reading_height():
    generate_css = (ROOT / "static" / "css" / "s21-generate.css").read_text(encoding="utf-8")

    assert "--methodology-readable-height: max(520px, calc(100vh - 360px));" in generate_css
    assert "--methodology-fragment-height: max(640px, calc(100vh - 320px));" in generate_css
    assert "min-height: calc(100vh - 220px);" in generate_css
    assert "min-height: min(620px, var(--methodology-readable-height));" in generate_css
    assert "max-height: var(--methodology-readable-height);" in generate_css
    assert "max-height: var(--methodology-fragment-height);" in generate_css
    assert ".methodology-history-details[open] .methodology-stage-list" in generate_css


def test_methodology_preview_uses_shared_markdown_renderer():
    js = (ROOT / "static" / "js" / "modules" / "methodologyPanel.js").read_text(encoding="utf-8")

    assert "window.renderMarkdownPreview" in js
    assert "function repairBrokenMermaidPreviewMarkdown" not in js
    assert "function renderBasicMarkdownHtml" not in js
    assert "function wrapMarkdownTables(root)" not in js


def test_mermaid_diagram_contract_is_scrollable_and_shared_with_images():
    js = (ROOT / "static" / "js" / "modules" / "markdownRendering.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "styles.css").read_text(encoding="utf-8")
    s21_css = (ROOT / "static" / "css" / "s21-markdown.css").read_text(encoding="utf-8")
    panel_js = (ROOT / "static" / "js" / "modules" / "methodologyPanel.js").read_text(encoding="utf-8")

    assert "function renderMarkdownPreview" in js
    assert "function wrapDiagramImages" in js
    assert "mermaid.parse(code)" in js
    assert "function normalizeMermaidEdgeLabelLine" in js
    assert "function normalizeMermaidArrowSyntax" in js
    assert "function normalizeSequenceMermaidStatements" in js
    assert "\\u2192" in js
    assert "\\u2014" in js
    assert "function looksLikeMermaidCode" in js
    assert "function getMermaidCodeBlocks" in js
    assert "root.querySelectorAll('pre code')" in js
    assert "root.querySelectorAll('pre')" in js
    assert "looksLikeMermaidCode(codeBlock.textContent || '')" in js
    assert "function normalizeStrayLeadingSentenceDots" in js
    assert "function mermaidCodeMetrics" in js
    assert "function stabilizeRenderedMermaid" in js
    assert "function diagramRenderContext" in js
    assert "function openDiagramLightbox" in js
    assert "function diagramLightboxWidth" in js
    assert "sourceWidth * 1.12" in js
    assert "--diagram-lightbox-media-width" in js
    assert "function enableDiagramZoom" in js
    assert "enableDiagramZoom(holder, svgEl" in js
    assert "enableDiagramZoom(surface, img" in js
    assert "holder.dataset.diagramContext = renderContext" in js
    assert "renderMermaidDiagrams(container, renderOptions)" in js
    assert "function beginMarkdownRender" in js
    assert "function isMarkdownRenderCurrent" in js
    assert "function ignoreStaleRenderError" in js
    assert "container.dataset.markdownRenderToken" in js
    assert "await Promise.all(renderTasks)" in js
    assert "typesetMathJax(container, renderToken)" in js
    assert "holder.dataset.diagramOverflow = isWide ? 'scroll' : 'fit';" in js
    assert "holder.style.setProperty('--diagram-font-size', isWide ? profile.wideFontSize : profile.normalFontSize);" in js
    assert "fontSize: '14px'" in (ROOT / "static" / "js" / "main.js").read_text(encoding="utf-8")
    assert "diagramContext: 'methodology'" in panel_js
    assert "function isVisibleMarkdownPreview" in panel_js
    assert ".diagram-image-surface" in css
    assert "min-height: min(var(--diagram-min-height, 220px), 520px)" in css
    assert "data-diagram-overflow=\"scroll\"" in css
    assert "max-width: min(var(--diagram-box-width, 920px), 100%)" in s21_css
    assert ".methodology-markdown-preview.markdown-preview .mermaid-diagram[data-diagram-size=\"tall\"]" in s21_css
    assert ".methodology-markdown-preview.markdown-preview .mermaid-diagram[data-diagram-context=\"methodology\"] svg" in s21_css
    assert "max-height: min(600px, calc(100vh - 260px))" in s21_css
    assert ".diagram-zoom-control" in s21_css
    assert ".diagram-lightbox" in s21_css
    assert "width: var(--diagram-lightbox-media-width, 806px) !important;" in s21_css
    assert "body.s21-product .diagram-lightbox-media .node rect" in s21_css
    assert "cursor: zoom-in" in s21_css


def test_generator_regeneration_tab_has_section_scoped_comments():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    main_js = (ROOT / "static" / "js" / "main.js").read_text(encoding="utf-8")
    tabs_js = (ROOT / "static" / "js" / "modules" / "generationResultTabs.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "s21-generate.css").read_text(encoding="utf-8")

    assert "id=\"regenerationSectionSelector\"" in html
    assert "id=\"regenerationScopeDetails\"" in html
    assert "id=\"regenerationScopeDetails\" class=\"s21-regeneration-panel regeneration-scope-panel\" open" not in html
    assert "id=\"regenerationGlobalComment\"" in html
    assert "id=\"regenToc\"" in html
    assert "id=\"readmeModeCompare\"" in html
    assert "regeneration-scope-filter" in html
    assert "Фильтр разделов" in html
    assert "Общая правка" in html
    assert "<details" in html
    assert "id=\"regenerationSectionComment\"" not in html
    assert "addRegenerationSectionComment()" not in html
    assert "function extractRegenerationSections" in tabs_js
    assert "function toggleRegenerationSectionForm" in tabs_js
    assert "regenerationStore" in tabs_js
    assert "sectionDrafts" in tabs_js
    assert "function saveRegenerationSectionDrafts" in tabs_js
    assert "function updateRegenerationSectionDrafts" in tabs_js
    assert "function rememberRegenerationInstructions" in tabs_js
    assert "function getActiveResultTabName" in tabs_js
    assert "function renderRegenerationReadme" in tabs_js
    assert "function renderRegenerationComparison" in tabs_js
    assert "function renderRegenerationComparisonPreview" not in tabs_js
    assert "regeneration-compare-rendered" not in tabs_js
    assert "function isMethodologyReviewEnabledForResults" in tabs_js
    assert "function buildRegenerationComments" in tabs_js
    assert "readmeComparisonActive: true" in tabs_js
    assert "getRegeneratedMarkdownFromState" in tabs_js
    assert "data-regen-change-for" in tabs_js
    assert "data-regen-keep-for" in tabs_js
    assert "Что исправить" in tabs_js
    assert "Что оставить" in tabs_js
    assert "regenerationGlobalComment" in tabs_js
    assert "Общая правка:" in tabs_js
    assert "renderReadmeToc(regeneratedMarkdown, { tocId: 'regenToc', contentId: 'regenContent' })" in tabs_js
    assert "function buildRegenerationLineDiff" in tabs_js
    assert "Уже применённые правки предыдущих перегенераций" in tabs_js
    assert "Текущие правки имеют приоритет" in tabs_js
    assert "window.buildRegenerationComments" in main_js
    assert ".regeneration-global-comment" in css
    assert ".regeneration-section-form" in css
    assert ".regeneration-section-option.is-selected" in css
    assert ".regeneration-scope-summary" in css
    assert ".regeneration-scope-filter" in css
    assert "border-radius: 999px;" in css
    assert "background: var(--s21-accent-soft);" in css
    assert ".regeneration-history-note" in css
    assert ".regeneration-compare-view" in css
    assert ".regeneration-compare-rendered" not in css
    assert ".regeneration-compare-document" in css
    assert ".regeneration-diff-line.is-insert" in css
    assert ".regeneration-diff-line.is-delete" in css
    assert "body.s21-product.page-generate .result-view-actions > .btn" in css
    assert "body.s21-product.page-generate .generator-subbar .right .btn" in css
    assert "body.s21-product.page-generate .generated-data-summary" in css
    assert "color: var(--s21-ink-2) !important;" in css


def test_generator_regeneration_updates_data_and_is_hidden_in_methodology_mode():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    main_js = (ROOT / "static" / "js" / "main.js").read_text(encoding="utf-8")
    tabs_js = (ROOT / "static" / "js" / "modules" / "generationResultTabs.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "s21-generate.css").read_text(encoding="utf-8")

    assert "data-result-tab=\"regen\"" in html
    assert "regen-only-action" in html
    assert "function isMethodologyReviewEnabled" in main_js
    assert "function updateRegenerationAvailability" in main_js
    assert "function getWorkflowCapability" in main_js
    assert "getWorkflowCapability('project_regeneration')" in main_js
    assert "workflowCapabilities" in tabs_js
    assert "methodology-regeneration-disabled" in main_js
    assert "renderGeneratedDataTab(currentResult || { markdown: data.regenerated_md })" in main_js
    assert "window.rememberRegenerationInstructions?.(submittedInstructions)" in main_js
    assert "data.accepted === false" in main_js
    assert "rubric_regression" in main_js
    assert "Перегенерация не применена" in main_js
    assert "window.renderRegenerationReadme(data.regenerated_md)" in main_js
    assert "regeneratedMarkdown: window.regeneratedMarkdown || null" in main_js
    assert "function extractPracticeTasksFromMarkdown" in tabs_js
    assert "Практические задачи:" in tabs_js
    assert "body.s21-product.page-generate.methodology-regeneration-disabled .regen-only-action" in css


def test_generator_ui_uses_state_stores_before_split_modules():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    stores_js = (ROOT / "static" / "js" / "modules" / "stateStores.js").read_text(encoding="utf-8")
    main_js = (ROOT / "static" / "js" / "main.js").read_text(encoding="utf-8")

    assert "modules/stateStores.js" in html
    assert html.index("modules/stateStores.js") < html.index("modules/generationResultTabs.js")
    assert "generationStore" in stores_js
    assert "resultStore" in stores_js
    assert "regenerationStore" in stores_js
    assert "workflowProfileStore" in stores_js
    assert "STANDARD_WORKFLOW_PROFILE" in stores_js
    assert "METHODOLOGY_WORKFLOW_PROFILE" in stores_js
    assert "project_regeneration: true" in stores_js
    assert "bindLegacyWindowState" in stores_js
    assert "window.ContentGenStores" in main_js


def test_generator_form_hides_removed_optional_fields_and_defaults_to_russian():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    form_js = (ROOT / "static" / "js" / "modules" / "generationFormState.js").read_text(encoding="utf-8")
    validation_js = (ROOT / "static" / "js" / "utils" / "validation.js").read_text(encoding="utf-8")

    assert 'id="language"' not in html
    assert "Reference project hint" not in html
    assert "Reference practice hint" not in html
    assert 'id="gitlabLink"' not in html
    assert "GitLab / Google docs" not in html
    assert "runParamLanguage" not in html
    assert "language: 'ru'" in form_js
    assert "getInputValue('language')" not in form_js
    assert "function setInputValue" in form_js
    assert "function listFieldValue" in form_js
    assert "document.getElementById('requiredTools').value" not in form_js
    assert "document.getElementById('projectType').value" not in form_js
    assert "Выберите язык" not in validation_js


def test_generator_markdown_tables_task_lists_and_mermaid_stay_light_and_readable():
    main_js = (ROOT / "static" / "js" / "main.js").read_text(encoding="utf-8")
    markdown_js = (ROOT / "static" / "js" / "modules" / "markdownRendering.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "css" / "s21-markdown.css").read_text(encoding="utf-8")
    generate_css = (ROOT / "static" / "css" / "s21-generate.css").read_text(encoding="utf-8")

    assert "theme: 'base'" in main_js
    assert "theme: 'dark'" not in main_js
    assert "suppressErrorRendering: true" in main_js
    assert "background: var(--s21-surface-2) !important;" in css
    assert "body.s21-product .markdown-preview table {" in css
    assert "overflow-x: hidden;" in css
    assert "body.s21-product .markdown-preview > table" in css
    assert "body.s21-product.page-generate #readmeContent" in generate_css
    assert "body.s21-product .markdown-preview mjx-container" in css
    assert "body.s21-product .methodology-markdown-preview mjx-container svg path" in css
    assert "width: min(920px, 100%) !important;" in css
    assert "methodology-readme-fragment-section .methodology-markdown-preview" in generate_css
    assert "width: max-content;" in css
    assert "min-width: 220px;" in css
    assert "body.s21-product .markdown-preview table th *" in css
    assert "font-size: var(--diagram-font-size, 14px)" in css
    assert "function sentenceCaseCaption" in markdown_js
    assert r"^(?:табл\.?|таблица)(?:\s|\d|[:.—-]|$)" in markdown_js
    assert "const isDiagramAlt" in markdown_js
    assert "диаграмма|схема|процесс|алгоритм" in markdown_js
    assert "width: min(920px, 100%) !important;" in css
    assert "max-height: 560px !important;" in css
    assert "body.s21-product .markdown-preview li.task-list-item" in css
    assert "grid-template-columns: 18px minmax(0, 1fr);" in css
    assert "min-width: 16px !important;" in css
    assert "function wrapTaskListItemContent" in markdown_js
    assert "task-list-content" in markdown_js
    assert ".task-list-content" in css
    assert "grid-column: 2;" in css
    assert "overflow-wrap: anywhere !important;" in css
    assert "fill: var(--s21-surface) !important;" in css
    assert "stroke-width: 1.45px !important;" in css
    assert "stroke-width: 1.25px !important;" in css
    assert "vector-effect: non-scaling-stroke !important;" in css
    assert "overflow-y: auto !important;" in css
    assert "body.s21-product .diagram-lightbox-media foreignObject div" in css
    assert "align-items: center !important;" in css
    assert ".flowchart-label" in css


def test_path_like_values_do_not_wrap_one_character_per_line():
    css = (ROOT / "static" / "css" / "styles.css").read_text(encoding="utf-8")
    methodology_js = (ROOT / "static" / "js" / "modules" / "methodologyPanel.js").read_text(encoding="utf-8")

    assert ".markdown-preview :not(pre) > code" in css
    assert ".generated-data-path" in css
    assert ".methodology-artifact-list code.path-token" in css
    assert "white-space: nowrap !important;" in css
    assert "overflow-x: auto;" in css
    assert "word-break: normal !important;" in css
    assert "overflow-wrap: normal !important;" in css
    assert 'class="path-token"' in methodology_js


def test_auth_guard_preserves_requested_page_via_next():
    """P5: an unauthenticated hit on a protected tool redirects to login with a
    ?next back to the page, and login honours a safe same-origin /app next."""

    auth_cookie = (PKG / "api" / "integrations" / "auth_cookie.py").read_text(encoding="utf-8")
    login_html = (ROOT / "static" / "login.html").read_text(encoding="utf-8")

    # middleware carries the attempted path forward
    assert 'RedirectResponse(f"/?next={quote(path, safe=' in auth_cookie
    assert "from urllib.parse import quote" in auth_cookie

    # login resolves ?next safely (same-origin /app only) and uses it for both redirects
    assert "function loginRedirectTarget()" in login_html
    assert "next.startsWith('/app') && !next.startsWith('//')" in login_html
    assert "window.location.replace(loginRedirectTarget())" in login_html
    assert "window.location.replace('/app')" not in login_html


def test_curriculum_plan_picker_is_accessible_and_auto_loads():
    """P4/P5: the saved-UP picker has an associated label and loads on change
    (no second click), and the upload card is keyboard-operable."""

    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

    # label is associated with the select (a11y)
    assert '<label for="persistedCurriculumPlan">УП из базы</label>' in html
    # selecting a plan loads it immediately — the extra "Загрузить УП" button is gone
    assert 'id="persistedCurriculumPlan" onchange="if (this.value) loadPersistedCurriculumPlan()"' in html
    assert ">Загрузить УП</button>" not in html
    # no empty spacer label hack remains
    assert "<label>&nbsp;</label>" not in html

    # the curriculum upload card is reachable and operable from the keyboard
    assert 'class="file-upload s21-accent-upload generator-upload-card" role="button" tabindex="0"' in html
    assert "onkeydown=\"if ((event.key === 'Enter' || event.key === ' ')" in html


def test_shared_design_tokens_are_linked_on_every_surface():
    """P1: catalog and auditor render from the same s21 design tokens as the
    generator — one palette, one source of truth."""

    base_html = (PKG / "catalog" / "viewer" / "templates" / "base.html").read_text(encoding="utf-8")
    catalog_css = (PKG / "catalog" / "viewer" / "static" / "styles.css").read_text(encoding="utf-8")
    audit_rendering = (PKG / "audit" / "web_rendering.py").read_text(encoding="utf-8")
    audit_report_css = (PKG / "audit" / "templates" / "report.css").read_text(encoding="utf-8")

    # catalog links the shared tokens + alias layer before its own stylesheet
    assert "/static/css/s21-tokens.css?v=" in base_html
    assert "/static/css/s21-aliases.css?v=" in base_html
    assert base_html.index("s21-tokens.css") < base_html.index("{{ base }}/static/styles.css")

    # catalog no longer declares its own palette :root (tokens own it now)
    assert "--bg: #f4f1ea" not in catalog_css
    assert "--accent: #0e8f6f" not in catalog_css
    # and no warm literals leaked into component rules
    assert "#0e8f6f" not in catalog_css
    assert "rgba(44, 42, 37" not in catalog_css

    # auditor links the shared tokens and maps its vars onto them
    assert "/static/css/s21-tokens.css?v=" in audit_rendering
    assert "--accent: var(--s21-accent);" in audit_report_css
    assert "--bg: var(--s21-bg);" in audit_report_css

    # the alias layer file exists and maps catalog/auditor names onto s21 tokens
    aliases = (ROOT / "static" / "css" / "s21-aliases.css").read_text(encoding="utf-8")
    assert "--accent: var(--s21-accent);" in aliases
    assert "--ink: var(--s21-ink);" in aliases


def test_ecosystem_nav_links_every_module_on_every_surface():
    """P2: catalog and auditor carry the same cross-module top nav as the
    generator, so navigation never dead-ends inside a module."""

    from content_factory.audit.web_app import _render_topbar
    from content_factory.catalog.viewer.route_zones import get_ecosystem_nav

    module_hrefs = [
        "/app",
        "/app/generate",
        "/app/auditor",
        "/app/translate",
        "/app/curriculum",
        "/app/spravochnik",
        "/app/instruction",
    ]

    # catalog: nav is built server-side and rendered in base.html
    eco = get_ecosystem_nav("catalog")
    assert [item["href"] for item in eco] == module_hrefs
    assert sum(1 for item in eco if item["active"]) == 1
    assert next(item for item in eco if item["href"] == "/app/spravochnik")["active"] is True

    base_html = (PKG / "catalog" / "viewer" / "templates" / "base.html").read_text(encoding="utf-8")
    assert 'class="eco-nav"' in base_html
    assert "{% for item in ecosystem_nav %}" in base_html

    # auditor: same links, Аудитор active
    topbar = _render_topbar()
    for href in module_hrefs:
        assert f'href="{href}"' in topbar
    assert '<a class="eco-nav-link active" href="/app/auditor">Аудитор</a>' in topbar


def test_s21_brandbook_text_rules_are_enforced():
    css_dir = ROOT / "static" / "css"
    css_files = list(css_dir.glob("s21-*.css")) + [css_dir / "auth-light.css"]
    offenders = []

    for path in css_files:
        css = path.read_text(encoding="utf-8")
        for match in re.finditer(r"letter-spacing:\s*([^;]+);", css):
            value = match.group(1).strip()
            if not value.startswith("0"):
                offenders.append(f"{path.name}: letter-spacing {value}")
        if re.search(r"font-size:\s*clamp\(", css):
            offenders.append(f"{path.name}: viewport-scaled font-size")
        if re.search(r"font-size:\s*[\d.]+vw", css):
            offenders.append(f"{path.name}: vw font-size")
        if re.search(r"font-weight:\s*(750|850)\b", css):
            offenders.append(f"{path.name}: non-token font-weight")

    generate_css = (css_dir / "s21-generate.css").read_text(encoding="utf-8")
    markdown_css = (css_dir / "s21-markdown.css").read_text(encoding="utf-8")
    methodology_css = (css_dir / "s21-methodology.css").read_text(encoding="utf-8")

    assert not offenders
    assert "#11201a" not in generate_css
    assert "#f4fff9" not in generate_css
    assert "#334238" not in markdown_css
    assert "#bfe8d2" not in generate_css
    assert "background: var(--s21-bg);" in generate_css
    assert "background: var(--s21-surface-2);" in generate_css
    assert "color: var(--s21-ink);" in generate_css
    assert "body.s21-product .methodology-artifact-text" in methodology_css
    assert "color: var(--s21-ink-2) !important;" in methodology_css
