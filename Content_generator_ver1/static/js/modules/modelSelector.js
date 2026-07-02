(function () {
    const STORAGE_KEY = 'selected_model';
    const MODELS = new Set(['polza', 'deepseek', 'gigachat']);
    const MODEL_ALIASES = {
        gpt: 'polza',
        openai: 'polza',
        openrouter: 'polza',
        open_router: 'polza',
    };
    const PROVIDER_BY_MODEL = {
        polza: 'polza',
        deepseek: 'deepseek',
        gigachat: 'gigachat',
    };

    function normalizeModel(value) {
        const raw = String(value || '').trim().toLowerCase();
        const normalized = MODEL_ALIASES[raw] || raw;
        return MODELS.has(normalized) ? normalized : 'polza';
    }

    function closeModelMenus(exceptPicker = null) {
        document.querySelectorAll('.dashboard-model-picker.is-open').forEach((picker) => {
            if (picker === exceptPicker) return;
            picker.classList.remove('is-open');
            picker.querySelector('.dashboard-model-button')?.setAttribute('aria-expanded', 'false');
            const menu = picker.querySelector('.dashboard-model-menu');
            if (menu) menu.hidden = true;
        });
    }

    function applyModelToPicker(picker, model) {
        picker.dataset.selectedModel = model;
        picker.querySelectorAll('.dashboard-model-menu [data-model]').forEach((option) => {
            option.setAttribute('aria-selected', String(option.dataset.model === model));
        });
    }

    function toggleDashboardModelMenu(button) {
        const picker = button.closest('.dashboard-model-picker');
        if (!picker) return;
        const nextOpen = !picker.classList.contains('is-open');
        closeModelMenus(picker);
        picker.classList.toggle('is-open', nextOpen);
        button.setAttribute('aria-expanded', String(nextOpen));
        const menu = picker.querySelector('.dashboard-model-menu');
        if (menu) menu.hidden = !nextOpen;
    }

    function setDashboardModel(value) {
        const model = normalizeModel(value);
        document.querySelectorAll('.dashboard-model-picker').forEach((picker) => {
            applyModelToPicker(picker, model);
        });
        closeModelMenus();
        localStorage.setItem(STORAGE_KEY, model);
        window.dispatchEvent(new CustomEvent('dashboard:model-change', {
            detail: { model, provider: PROVIDER_BY_MODEL[model] },
        }));
    }

    function getSelectedDashboardModel() {
        return normalizeModel(localStorage.getItem(STORAGE_KEY));
    }

    function getSelectedLlmProvider() {
        return PROVIDER_BY_MODEL[getSelectedDashboardModel()];
    }

    function restoreDashboardModel() {
        setDashboardModel(getSelectedDashboardModel());
    }

    window.setDashboardModel = setDashboardModel;
    window.toggleDashboardModelMenu = toggleDashboardModelMenu;
    window.getSelectedDashboardModel = getSelectedDashboardModel;
    window.getSelectedLlmProvider = getSelectedLlmProvider;
    window.restoreDashboardModel = restoreDashboardModel;

    document.addEventListener('click', (event) => {
        if (!(event.target instanceof Element) || !event.target.closest('.dashboard-model-picker')) {
            closeModelMenus();
        }
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            closeModelMenus();
        }
    });
    document.addEventListener('DOMContentLoaded', restoreDashboardModel);
}());
