/**
 * Утилиты для санитизации HTML и защиты от XSS.
 * 
 * Для ПК-версии: базовая санитизация без внешних зависимостей.
 */

/**
 * Экранирует HTML-специальные символы.
 * 
 * @param {string} text - Текст для экранирования
 * @returns {string} Экранированный текст
 */
function escapeHtml(text) {
    if (text == null) return '';
    if (typeof text !== 'string') {
        text = String(text);
    }
    
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Безопасно вставляет HTML в элемент, экранируя текст.
 * 
 * @param {HTMLElement} element - Элемент для вставки
 * @param {string} html - HTML для вставки (будет экранирован)
 */
function safeSetInnerHTML(element, html) {
    if (!element) return;
    
    // Экранируем весь HTML как текст
    element.textContent = html;
}

/**
 * Безопасно вставляет HTML с поддержкой простых тегов.
 * Разрешает только безопасные теги для UI-блоков, Markdown и сворачиваемых секций.
 * 
 * @param {HTMLElement} element - Элемент для вставки
 * @param {string} html - HTML для вставки
 */
function safeSetHTML(element, html) {
    if (!element) return;
    if (!html) {
        element.innerHTML = '';
        return;
    }
    
    // Разрешенные теги и их атрибуты
    const allowedTags = {
        'div': ['class', 'id', 'style'],
        'section': ['class', 'id', 'style'],
        'span': ['class', 'id', 'style'],
        'p': ['class', 'id', 'style'],
        'details': ['class', 'id', 'open'],
        'summary': ['class', 'id'],
        'strong': [],
        'b': [],
        'em': [],
        'i': [],
        'ul': ['class'],
        'ol': ['class'],
        'li': ['class'],
        'br': [],
        'code': ['class'],
        'pre': ['class'],
        'h1': ['class'],
        'h2': ['class'],
        'h3': ['class'],
        'h4': ['class'],
        'h5': ['class'],
        'h6': ['class'],
        'a': ['href', 'class'],
        'img': ['src', 'alt', 'class', 'style'],
        'table': ['class'],
        'thead': ['class'],
        'tbody': ['class'],
        'tr': ['class'],
        'td': ['class', 'colspan', 'rowspan'],
        'th': ['class', 'colspan', 'rowspan'],
        'button': ['type', 'class', 'id', 'style', 'data-filter', 'onclick'],
        'label': ['class', 'id', 'style', 'for'],
        'input': ['type', 'class', 'id', 'style', 'checked', 'onchange'],
    };
    
    // Простая санитизация: разрешаем только безопасные теги
    // Для сложных случаев лучше использовать DOMPurify, но для ПК-версии это достаточно
    const sanitized = html
        .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, '') // Удаляем script
        .replace(/<iframe\b[^<]*(?:(?!<\/iframe>)<[^<]*)*<\/iframe>/gi, '') // Удаляем iframe
        .replace(/on\w+\s*=\s*["'][^"']*["']/gi, '') // Удаляем event handlers
        .replace(/javascript:/gi, ''); // Удаляем javascript: протокол
    
    // Используем DOMParser для безопасного парсинга
    try {
        const parser = new DOMParser();
        const doc = parser.parseFromString(sanitized, 'text/html');
        
        // Рекурсивно очищаем от неразрешенных тегов
        function cleanNode(node) {
            if (node.nodeType === Node.TEXT_NODE) {
                return node.cloneNode(true);
            }
            
            if (node.nodeType === Node.ELEMENT_NODE) {
                const tagName = node.tagName.toLowerCase();
                
                // Если тег не разрешен, заменяем на текст
                if (!allowedTags[tagName]) {
                    return document.createTextNode(node.textContent || '');
                }
                
                // Создаем новый элемент
                const cleanElement = document.createElement(tagName);
                
                // Копируем разрешенные атрибуты. data-* и aria-* нужны для интерактивных
                // компонентов после безопасной вставки HTML, например вкладок предпросмотра.
                const allowedAttrs = allowedTags[tagName] || [];
                for (const attr of node.attributes) {
                    const attrName = attr.name.toLowerCase();
                    const isAllowedAttribute = allowedAttrs.includes(attrName)
                        || attrName.startsWith('data-')
                        || attrName.startsWith('aria-')
                        || attrName === 'role';
                    if (isAllowedAttribute) {
                        // Дополнительная проверка для href и src
                        if (attrName === 'href' || attrName === 'src') {
                            const value = attr.value.toLowerCase();
                            if (value.startsWith('javascript:') || value.startsWith('data:text/html')) {
                                continue; // Пропускаем опасные протоколы
                            }
                        }
                        cleanElement.setAttribute(attr.name, escapeHtml(attr.value));
                    }
                }
                
                // Рекурсивно обрабатываем дочерние элементы
                for (const child of node.childNodes) {
                    const cleanChild = cleanNode(child);
                    if (cleanChild) {
                        cleanElement.appendChild(cleanChild);
                    }
                }
                
                return cleanElement;
            }
            
            return null;
        }
        
        // Очищаем body
        const body = doc.body;
        const fragment = document.createDocumentFragment();
        for (const child of body.childNodes) {
            const cleanChild = cleanNode(child);
            if (cleanChild) {
                fragment.appendChild(cleanChild);
            }
        }
        
        element.innerHTML = '';
        element.appendChild(fragment);
    } catch (e) {
        // Если парсинг не удался, используем простое экранирование
        console.warn('Ошибка санитизации HTML, используется простое экранирование:', e);
        element.textContent = html;
    }
}

/**
 * Безопасно вставляет HTML для сообщений об ошибках.
 * Разрешает только простые теги форматирования.
 * 
 * @param {HTMLElement} element - Элемент для вставки
 * @param {string} message - Сообщение (может содержать HTML)
 */
function safeSetErrorMessage(element, message) {
    if (!element) return;
    
    // Для сообщений об ошибках используем простое экранирование
    // чтобы избежать проблем с форматированием
    const escaped = escapeHtml(message);
    element.innerHTML = `<div class="error-msg">❌ ${escaped}</div>`;
}

/**
 * Безопасно вставляет HTML для Markdown контента.
 * Используется после рендеринга Markdown через marked.js.
 * 
 * @param {HTMLElement} element - Элемент для вставки
 * @param {string} html - HTML из Markdown
 */
function safeSetMarkdownHTML(element, html) {
    if (!element) return;
    if (!html) {
        element.innerHTML = '';
        return;
    }
    
    // Для Markdown используем более мягкую санитизацию
    // так как marked.js уже генерирует безопасный HTML
    // Но все равно удаляем опасные элементы
    
    const sanitized = html
        .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, '')
        .replace(/<iframe\b[^<]*(?:(?!<\/iframe>)<[^<]*)*<\/iframe>/gi, '')
        .replace(/on\w+\s*=\s*["'][^"']*["']/gi, '')
        .replace(/javascript:/gi, '');
    
    element.innerHTML = sanitized;
}

// Экспортируем функции в глобальную область видимости
window.sanitize = {
    escapeHtml,
    safeSetInnerHTML,
    safeSetHTML,
    safeSetErrorMessage,
    safeSetMarkdownHTML
};
