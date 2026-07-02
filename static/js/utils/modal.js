/**
 * Модальные окна для подтверждений действий
 */

class ModalManager {
    constructor() {
        this.overlay = null;
        this.init();
    }

    init() {
        if (this.overlay) {
            return this.overlay;
        }

        const existingOverlay = document.getElementById('modal-overlay');
        if (existingOverlay) {
            this.overlay = existingOverlay;
            return this.overlay;
        }

        if (!document.body) {
            document.addEventListener('DOMContentLoaded', () => this.init(), { once: true });
            return null;
        }

        // Создаем overlay для модальных окон
        this.overlay = document.createElement('div');
        this.overlay.id = 'modal-overlay';
        document.body.appendChild(this.overlay);

        return this.overlay;
    }

    confirm(title, message, confirmText = 'Подтвердить', cancelText = 'Отмена') {
        return new Promise((resolve) => {
            const overlay = this.init();
            if (!overlay) {
                document.addEventListener('DOMContentLoaded', async () => {
                    resolve(await this.confirm(title, message, confirmText, cancelText));
                }, { once: true });
                return;
            }

            const modal = document.createElement('div');
            modal.className = 's21-confirm-modal';

            modal.innerHTML = `
                <h3 class="s21-confirm-title">${title}</h3>
                <p class="s21-confirm-message">${message}</p>
                <div class="s21-confirm-actions">
                    <button class="btn btn-secondary" data-action="cancel">
                        ${cancelText}
                    </button>
                    <button class="btn btn-danger" data-action="confirm">
                        ${confirmText}
                    </button>
                </div>
            `;

            const handleClick = (e) => {
                const action = e.target.getAttribute('data-action');
                if (action === 'confirm') {
                    this.close();
                    resolve(true);
                } else if (action === 'cancel') {
                    this.close();
                    resolve(false);
                }
            };

            modal.addEventListener('click', handleClick);
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) {
                    this.close();
                    resolve(false);
                }
            });

            overlay.appendChild(modal);
            overlay.style.display = 'flex';

            // Закрытие по Escape
            const handleEscape = (e) => {
                if (e.key === 'Escape') {
                    this.close();
                    resolve(false);
                    document.removeEventListener('keydown', handleEscape);
                }
            };
            document.addEventListener('keydown', handleEscape);
        });
    }

    close() {
        if (!this.overlay) {
            return;
        }

        this.overlay.style.display = 'none';
        this.overlay.innerHTML = '';
    }
}

// Создаем глобальный экземпляр
window.modal = new ModalManager();

