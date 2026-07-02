/**
 * Toast-уведомления для пользователя
 */

class ToastManager {
    constructor() {
        this.container = null;
        this.init();
    }

    init() {
        if (this.container) {
            return this.container;
        }

        const existingContainer = document.getElementById('toast-container');
        if (existingContainer) {
            this.container = existingContainer;
            return this.container;
        }

        if (!document.body) {
            document.addEventListener('DOMContentLoaded', () => this.init(), { once: true });
            return null;
        }

        // Создаем контейнер для toast-уведомлений
        this.container = document.createElement('div');
        this.container.id = 'toast-container';
        document.body.appendChild(this.container);
        return this.container;
    }

    show(message, type = 'info', duration = 3000) {
        const container = this.init();
        if (!container) {
            document.addEventListener('DOMContentLoaded', () => this.show(message, type, duration), { once: true });
            return null;
        }

        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;

        const icons = {
            success: '✓',
            error: '!',
            warning: '!',
            info: 'i'
        };

        toast.innerHTML = `
            <span class="toast-icon">${icons[type] || icons.info}</span>
            <span class="toast-message">${message}</span>
            <button class="toast-close" onclick="this.parentElement.remove()">×</button>
        `;

        container.appendChild(toast);

        // Автоматическое удаление
        if (duration > 0) {
            setTimeout(() => {
                toast.classList.add('is-exiting');
                setTimeout(() => toast.remove(), 300);
            }, duration);
        }

        return toast;
    }

    success(message, duration = 3000) {
        return this.show(message, 'success', duration);
    }

    error(message, duration = 5000) {
        return this.show(message, 'error', duration);
    }

    warning(message, duration = 4000) {
        return this.show(message, 'warning', duration);
    }

    info(message, duration = 3000) {
        return this.show(message, 'info', duration);
    }
}

// Создаем глобальный экземпляр
window.toast = new ToastManager();

