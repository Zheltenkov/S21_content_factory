// Centralized auth/session handling for protected application pages.
// It clears stale JWT/session state and redirects to login on server-side auth failures.
(function () {
    if (window.ContentGenAuth) return;

    const AUTH_STORAGE_KEYS = [
        'auth_token',
        'user_id',
        'username',
        'email',
        'session_id',
        'user',
        'user_info'
    ];
    const API_URL = window.API_URL || window.ContentGenApiUrl || `${window.location.origin}/api/v1`;
    const nativeFetch = window.fetch.bind(window);
    const PROTECTED_TOOL_PATHS = new Set(['/app/auditor', '/app/curriculum', '/app/spravochnik']);
    let redirecting = false;
    let cookieSyncPromise = null;

    function clearAuthState() {
        AUTH_STORAGE_KEYS.forEach((key) => localStorage.removeItem(key));
        sessionStorage.removeItem('generation_state');
        sessionStorage.removeItem('checker_results');
    }

    function getToken() {
        return localStorage.getItem('auth_token') || '';
    }

    function getAuthHeaders(extraHeaders) {
        const token = getToken();
        return {
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
            ...(extraHeaders || {})
        };
    }

    function resolveRequestUrl(input) {
        const raw = typeof input === 'string' ? input : input?.url;
        if (!raw) return null;
        try {
            return new URL(raw, window.location.origin);
        } catch {
            return null;
        }
    }

    function isApiRequest(input) {
        const url = resolveRequestUrl(input);
        return !!url && url.origin === window.location.origin && url.pathname.startsWith('/api/v1/');
    }

    function isPublicAuthRequest(input) {
        const url = resolveRequestUrl(input);
        if (!url) return false;
        return [
            '/api/v1/auth/login',
            '/api/v1/auth/register',
            '/api/v1/auth/forgot-password',
            '/api/v1/auth/reset-password',
            '/api/v1/auth/password-reset'
        ].some((path) => url.pathname.startsWith(path));
    }

    async function responseMessage(response) {
        try {
            const data = await response.clone().json();
            if (typeof data?.detail === 'string') return data.detail;
            if (data?.detail) return JSON.stringify(data.detail);
        } catch {
            try {
                const text = await response.clone().text();
                if (text) return text;
            } catch {
                // Keep fallback below.
            }
        }
        return response.status === 401
            ? 'Сессия истекла. Войдите заново.'
            : `Ошибка авторизации: ${response.status}`;
    }

    function redirectToLogin(reason) {
        if (redirecting) return;
        redirecting = true;
        clearAuthState();
        if (reason) sessionStorage.setItem('auth_redirect_reason', reason);
        window.location.replace('/');
    }

    async function handleUnauthorized(response) {
        const message = await responseMessage(response);
        redirectToLogin(message);
        return message;
    }

    async function authFetch(input, init) {
        const response = await nativeFetch(input, init);
        if (
            response.status === 401
            && isApiRequest(input)
            && !isPublicAuthRequest(input)
        ) {
            await handleUnauthorized(response);
        }
        return response;
    }

    function ensureAuthPresent() {
        if (getToken()) return true;
        redirectToLogin('Требуется вход в систему.');
        return false;
    }

    async function ensureValidSession() {
        if (!ensureAuthPresent()) return false;
        try {
            const response = await nativeFetch(`${API_URL}/auth/me`, {
                method: 'GET',
                headers: getAuthHeaders(),
                cache: 'no-store'
            });
            if (response.ok) {
                await ensureNavigationCookie();
                return true;
            }
            if (response.status === 401 || response.status === 403) {
                await handleUnauthorized(response);
                return false;
            }
            // Do not log out on transient server/network problems.
            return true;
        } catch (error) {
            console.warn('Session validation skipped:', error);
            return true;
        }
    }

    async function ensureNavigationCookie() {
        if (!ensureAuthPresent()) return false;
        if (cookieSyncPromise) return cookieSyncPromise;

        cookieSyncPromise = (async () => {
            const response = await nativeFetch(`${API_URL}/auth/session-cookie`, {
                method: 'POST',
                headers: getAuthHeaders(),
                cache: 'no-store'
            });
            if (response.ok) return true;
            if (response.status === 401 || response.status === 403) {
                await handleUnauthorized(response);
                return false;
            }
            // Keep navigation possible for freshly logged-in sessions that already have a cookie.
            console.warn('Navigation cookie sync skipped:', response.status);
            return true;
        })();

        try {
            return await cookieSyncPromise;
        } catch (error) {
            console.warn('Navigation cookie sync failed:', error);
            return true;
        } finally {
            cookieSyncPromise = null;
        }
    }

    function protectedToolPathForHref(href) {
        try {
            const url = new URL(href, window.location.origin);
            if (url.origin !== window.location.origin) return '';
            return PROTECTED_TOOL_PATHS.has(url.pathname) ? url.pathname : '';
        } catch {
            return '';
        }
    }

    function shouldHandleNavigationClick(event, link) {
        if (event.defaultPrevented || event.button !== 0) return false;
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return false;
        if (link.target && link.target !== '_self') return false;
        return Boolean(protectedToolPathForHref(link.href));
    }

    function attachProtectedToolNavigation() {
        document.addEventListener('click', async (event) => {
            const link = event.target?.closest?.('a[href]');
            if (!link || !shouldHandleNavigationClick(event, link)) return;

            event.preventDefault();
            const targetUrl = link.href;
            if (await ensureNavigationCookie()) {
                window.location.assign(targetUrl);
            }
        });
    }

    window.ContentGenAuth = {
        clearAuthState,
        getAuthHeaders,
        ensureAuthPresent,
        ensureValidSession,
        ensureNavigationCookie,
        handleUnauthorized,
        redirectToLogin,
        fetch: authFetch
    };
    window.fetch = authFetch;
    if (typeof window.getAuthHeaders !== 'function') {
        window.getAuthHeaders = getAuthHeaders;
    }

    attachProtectedToolNavigation();
    ensureValidSession();
})();
