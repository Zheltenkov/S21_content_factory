/**
 * Управление индикаторами загрузки и прогресс-барами
 */

class LoadingManager {
    constructor() {
        this.progressBars = new Map();
        this.generationStages = [
            { label: 'Контекст', start: 0, end: 20 },
            { label: 'План', start: 20, end: 35, tone: 'warm' },
            { label: 'Теория', start: 35, end: 60 },
            { label: 'Практика', start: 60, end: 78 },
            { label: 'Проверка', start: 78, end: 100 }
        ];
    }

    /**
     * Проверяет, что индикатор рисуется в компактной области генерации README.
     */
    isGenerationProgressContainer(container) {
        if (!container) return false;
        return container.id === 'logContent' || container.classList?.contains('generation-progress-strip');
    }

    /**
     * Создает компактную горизонтальную шкалу этапов генерации.
     */
    createGenerationPhaseProgress(container) {
        const progressId = `progress-${Date.now()}`;
        const progressBar = document.createElement('div');
        progressBar.id = progressId;
        progressBar.className = 'generation-phase-strip';
        progressBar.innerHTML = this.generationStages.map(stage => `
            <div class="generation-phase-card ${stage.tone === 'warm' ? 'is-warm' : ''}" data-start="${stage.start}" data-end="${stage.end}">
                <span class="generation-phase-label">${stage.label}</span>
                <div class="generation-phase-track">
                    <div class="generation-phase-fill"></div>
                </div>
            </div>
        `).join('');

        container.appendChild(progressBar);

        this.progressBars.set(progressId, {
            type: 'generation-phase',
            element: progressBar,
            stages: this.generationStages,
            cards: Array.from(progressBar.querySelectorAll('.generation-phase-card')),
            fills: Array.from(progressBar.querySelectorAll('.generation-phase-fill'))
        });

        return progressId;
    }

    /**
     * Показывает spinner в элементе
     */
    showSpinner(container, message = 'Загрузка...') {
        if (typeof container === 'string') {
            container = document.getElementById(container);
        }

        const spinnerId = `spinner-${Date.now()}`;
        const spinner = document.createElement('div');

        if (this.isGenerationProgressContainer(container)) {
            spinner.id = spinnerId;
            spinner.className = 'generation-activity';
            spinner.innerHTML = `
                <span class="generation-activity-dot"></span>
                <span>${message}</span>
            `;
            if (container) {
                container.appendChild(spinner);
            }
            return spinnerId;
        }

        spinner.id = spinnerId;
        spinner.className = 'loading-spinner';
        spinner.innerHTML = `
            <div class="spinner-wrapper">
                <div class="spinner"></div>
                <p class="loading-message">${message}</p>
            </div>
        `;

        if (container) {
            container.appendChild(spinner);
        }

        return spinnerId;
    }

    /**
     * Удаляет spinner
     */
    hideSpinner(spinnerId) {
        const spinner = document.getElementById(spinnerId);
        if (spinner) {
            spinner.remove();
        }
    }

    /**
     * Создает прогресс-бар
     */
    createProgressBar(containerId, label = 'Прогресс') {
        const container = document.getElementById(containerId);
        if (!container) return null;

        if (this.isGenerationProgressContainer(container)) {
            return this.createGenerationPhaseProgress(container);
        }

        const progressId = `progress-${Date.now()}`;
        const progressBar = document.createElement('div');
        progressBar.id = progressId;
        progressBar.className = 'progress-bar-container';
        progressBar.innerHTML = `
            <div class="progress-bar-head">
                <span class="progress-label">${label}</span>
                <span class="progress-percent">0%</span>
            </div>
            <div class="progress-bar-track">
                <div class="progress-bar-fill"></div>
            </div>
        `;

        container.appendChild(progressBar);

        this.progressBars.set(progressId, {
            element: progressBar,
            fill: progressBar.querySelector('.progress-bar-fill'),
            percent: progressBar.querySelector('.progress-percent')
        });

        return progressId;
    }

    /**
     * Обновляет прогресс-бар
     */
    updateProgress(progressId, percent) {
        const progress = this.progressBars.get(progressId);
        if (!progress) return;

        const clampedPercent = Math.max(0, Math.min(100, percent));
        if (progress.type === 'generation-phase') {
            progress.stages.forEach((stage, index) => {
                const range = Math.max(1, stage.end - stage.start);
                const localPercent = Math.max(0, Math.min(100, ((clampedPercent - stage.start) / range) * 100));
                const fill = progress.fills[index];
                const card = progress.cards[index];

                if (fill) {
                    fill.style.width = `${localPercent}%`;
                }
                if (card) {
                    card.classList.toggle('is-active', clampedPercent >= stage.start && clampedPercent < stage.end);
                    card.classList.toggle('is-complete', clampedPercent >= stage.end);
                }
            });
            return;
        }

        progress.fill.style.width = `${clampedPercent}%`;
        progress.percent.textContent = `${Math.round(clampedPercent)}%`;
    }

    /**
     * Удаляет прогресс-бар
     */
    removeProgressBar(progressId) {
        const progress = this.progressBars.get(progressId);
        if (progress) {
            progress.element.remove();
            this.progressBars.delete(progressId);
        }
    }

    /**
     * Показывает индикатор загрузки на кнопке
     */
    setButtonLoading(buttonId, loading = true, originalText = null) {
        const button = typeof buttonId === 'string' ? document.getElementById(buttonId) : buttonId;
        if (!button) return;

        if (loading) {
            button.dataset.originalText = originalText || button.textContent;
            button.disabled = true;
            button.innerHTML = `
                <span class="s21-button-spinner"></span>
                ${button.dataset.originalText}
            `;
        } else {
            button.disabled = false;
            button.textContent = button.dataset.originalText || originalText || 'Отправить';
        }
    }
}

// Создаем глобальный экземпляр
window.loading = new LoadingManager();
