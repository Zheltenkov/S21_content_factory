// Lightweight page transition accelerator.
// It prefetches a page only when the user shows intent to open it.
(function () {
    const APP_ROUTES = [
        '/app',
        '/app/generate',
        '/app/auditor',
        '/app/translate',
        '/app/curriculum',
        '/app/spravochnik',
        '/app/instruction'
    ];
    const prefetched = new Set();
    const parsedDocuments = new Set();
    const MAX_BACKGROUND_ROUTES = 4;

    function shouldPrefetch() {
        const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
        if (connection?.saveData) return false;
        if (connection?.effectiveType && /(^|-)2g$/.test(connection.effectiveType)) return false;
        return typeof document !== 'undefined' && document.visibilityState !== 'hidden';
    }

    function normalizePath(url) {
        try {
            const parsed = new URL(url, window.location.origin);
            return parsed.origin === window.location.origin ? parsed.pathname : '';
        } catch {
            return '';
        }
    }

    function prefetchLink(url, asType = '') {
        if (!url || prefetched.has(url)) return;
        prefetched.add(url);

        const link = document.createElement('link');
        link.rel = 'prefetch';
        link.href = url;
        if (asType) link.as = asType;
        link.crossOrigin = 'anonymous';
        document.head.appendChild(link);
    }

    function assetTypeFor(element) {
        if (element.tagName === 'SCRIPT') return 'script';
        if (element.tagName === 'LINK') return 'style';
        return '';
    }

    function collectStaticAssets(html) {
        const doc = new DOMParser().parseFromString(html, 'text/html');
        const nodes = doc.querySelectorAll('link[rel="stylesheet"][href], script[src]');
        return Array.from(nodes)
            .map((node) => {
                const raw = node.getAttribute('href') || node.getAttribute('src') || '';
                const url = new URL(raw, window.location.origin);
                if (url.origin !== window.location.origin || !url.pathname.startsWith('/static/')) {
                    return null;
                }
                return { url: url.pathname + url.search, asType: assetTypeFor(node) };
            })
            .filter(Boolean);
    }

    async function prefetchRoute(route, options = {}) {
        if (!shouldPrefetch()) return;
        const path = normalizePath(route);
        if (!APP_ROUTES.includes(path)) return;

        prefetchLink(path, 'document');
        if (parsedDocuments.has(path)) return;
        parsedDocuments.add(path);

        try {
            const response = await fetch(path, {
                credentials: 'same-origin',
                cache: options.priority ? 'reload' : 'force-cache',
                headers: { 'X-ContentGen-Prefetch': '1' },
            });
            if (!response.ok) return;
            const html = await response.text();
            collectStaticAssets(html).forEach((asset) => prefetchLink(asset.url, asset.asType));
        } catch (err) {
            console.debug('[Prefetch] route skipped:', path, err);
        }
    }

    function scheduleIdle(callback) {
        if ('requestIdleCallback' in window) {
            window.requestIdleCallback(callback, { timeout: 2500 });
            return;
        }
        window.setTimeout(callback, 900);
    }

    function attachNavigationPrefetch() {
        document.querySelectorAll('a[href^="/app"], button[data-prefetch-href]').forEach((element) => {
            const route = element.getAttribute('href') || element.dataset.prefetchHref || '';
            const handler = () => prefetchRoute(route, { priority: true });
            element.addEventListener('pointerenter', handler, { once: true, passive: true });
            element.addEventListener('focus', handler, { once: true });
            element.addEventListener('touchstart', handler, { once: true, passive: true });
        });
    }

    function prefetchBackgroundRoutes() {
        const current = normalizePath(window.location.href);
        APP_ROUTES
            .filter((route) => route !== current)
            .slice(0, MAX_BACKGROUND_ROUTES)
            .forEach((route, index) => {
                window.setTimeout(() => prefetchRoute(route), index * 350);
            });
    }

    function initPagePrefetch() {
        if (!shouldPrefetch()) return;
        attachNavigationPrefetch();
        // Background prefetch can compete with the current page CSS/JS on slow links.
        // Keep it opt-in and use intent-based prefetch by default.
        if (window.ContentGenEnableBackgroundPrefetch === true) {
            scheduleIdle(prefetchBackgroundRoutes);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initPagePrefetch, { once: true });
    } else {
        initPagePrefetch();
    }

    window.ContentGenPagePrefetch = {
        prefetchRoute,
        prefetchBackgroundRoutes,
    };
})();
