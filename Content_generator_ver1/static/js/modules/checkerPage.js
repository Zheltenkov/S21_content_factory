// Checker page shell.
// Focused feature modules own curriculum state, improvement modal, run/progress,
// README preview, diff rendering and metrics switching.

(function () {
    const token = localStorage.getItem('auth_token');
    if (!token) {
        window.location.replace('/');
        return;
    }

    if (typeof restoreCheckerResults === 'function') {
        restoreCheckerResults();
    }
})();

window.checkerPageState = window.checkerPageState || {
    improvementRequestId: null,
    improvementGenerationRequestId: null
};
window.improvedRubric = window.improvedRubric || null;

function goBackToMode() {
    window.location.href = '/app';
}

function clearCheckerFile() {
    const input = document.getElementById('readmeFile');
    if (input) input.value = '';

    const title = document.getElementById('checkerReadmeUploadTitle');
    const label = document.getElementById('readmeFileNameChecker');
    if (title) title.textContent = 'Загрузить README.md';
    if (label) label.textContent = 'Markdown-файл для проверки';
}

function hydrateCheckerUser() {
    const rawName = localStorage.getItem('username') || localStorage.getItem('email') || 'M. Кравцова';
    const displayName = rawName.includes('@') ? rawName.split('@')[0] : rawName;
    const nameEl = document.getElementById('checkerUserName');
    const initialsEl = document.getElementById('checkerUserInitials');

    if (nameEl) nameEl.textContent = displayName;
    if (initialsEl) {
        const initials = displayName
            .split(/\s+/)
            .filter(Boolean)
            .slice(0, 2)
            .map(part => part[0])
            .join('')
            .toUpperCase() || 'МК';
        initialsEl.textContent = initials;
    }
}

function getAuthHeadersForImprovement() {
    if (typeof window.getAuthHeaders === 'function') {
        return window.getAuthHeaders();
    }
    if (typeof getAuthHeaders === 'function') {
        return getAuthHeaders();
    }

    const token = localStorage.getItem('auth_token');
    if (token) {
        return {
            'Authorization': `Bearer ${token}`,
            'Content-Type': 'application/json'
        };
    }
    return {};
}

function setCheckerImprovementRequestId(requestId) {
    window.checkerPageState.improvementRequestId = requestId || null;
}

function setCheckerImprovementGenerationRequestId(requestId) {
    window.checkerPageState.improvementGenerationRequestId = requestId || null;
}

function getCheckerImprovementRequestId() {
    return window.checkerPageState.improvementRequestId;
}

function getCheckerImprovementGenerationRequestId() {
    return window.checkerPageState.improvementGenerationRequestId;
}

hydrateCheckerUser();

window.goBackToMode = goBackToMode;
window.clearCheckerFile = clearCheckerFile;
window.getAuthHeadersForImprovement = getAuthHeadersForImprovement;
window.setCheckerImprovementRequestId = setCheckerImprovementRequestId;
window.setCheckerImprovementGenerationRequestId = setCheckerImprovementGenerationRequestId;
window.getCheckerImprovementRequestId = getCheckerImprovementRequestId;
window.getCheckerImprovementGenerationRequestId = getCheckerImprovementGenerationRequestId;
