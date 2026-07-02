/**
 * Валидация форм
 */

class FormValidator {
    constructor() {
        this.errors = new Map();
    }

    /**
     * Валидация email
     */
    validateEmail(email) {
        if (!email || email.trim() === '') {
            return { valid: false, message: 'Email обязателен для заполнения' };
        }
        const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        if (!emailRegex.test(email)) {
            return { valid: false, message: 'Некорректный формат email' };
        }
        return { valid: true };
    }

    /**
     * Валидация обязательного поля
     */
    validateRequired(value, fieldName) {
        if (!value || (typeof value === 'string' && value.trim() === '')) {
            return { valid: false, message: `${fieldName} обязателен для заполнения` };
        }
        return { valid: true };
    }

    /**
     * Валидация длины текста
     */
    validateLength(value, min, max, fieldName) {
        if (!value) {
            return { valid: false, message: `${fieldName} обязателен для заполнения` };
        }
        const length = value.trim().length;
        if (min && length < min) {
            return { valid: false, message: `${fieldName} должен содержать минимум ${min} символов` };
        }
        if (max && length > max) {
            return { valid: false, message: `${fieldName} должен содержать максимум ${max} символов` };
        }
        return { valid: true };
    }

    /**
     * Валидация числа
     */
    validateNumber(value, min, max, fieldName) {
        const num = Number(value);
        if (isNaN(num)) {
            return { valid: false, message: `${fieldName} должен быть числом` };
        }
        if (min !== undefined && num < min) {
            return { valid: false, message: `${fieldName} должен быть не менее ${min}` };
        }
        if (max !== undefined && num > max) {
            return { valid: false, message: `${fieldName} должен быть не более ${max}` };
        }
        return { valid: true };
    }

    /**
     * Валидация URL
     */
    validateURL(url) {
        if (!url || url.trim() === '') {
            return { valid: true }; // URL опциональный
        }
        try {
            new URL(url);
            return { valid: true };
        } catch {
            return { valid: false, message: 'Некорректный формат URL' };
        }
    }

    /**
     * Показывает ошибку валидации
     */
    showError(fieldId, message) {
        const field = document.getElementById(fieldId);
        if (!field) return;

        // Удаляем предыдущую ошибку
        this.clearError(fieldId);

        // Добавляем класс ошибки
        field.classList.add('error');
        field.classList.remove('s21-input-focus', 's21-input-valid');

        // Создаем элемент с ошибкой
        const errorElement = document.createElement('div');
        errorElement.className = 'validation-error';
        errorElement.id = `${fieldId}-error`;
        errorElement.innerHTML = `<span>❌</span><span>${message}</span>`;

        // Вставляем после поля
        field.parentNode.appendChild(errorElement);

        this.errors.set(fieldId, errorElement);
    }

    /**
     * Очищает ошибку валидации
     */
    clearError(fieldId) {
        const field = document.getElementById(fieldId);
        if (field) {
            field.classList.remove('error');
            field.classList.remove('s21-input-valid');
        }

        const errorElement = document.getElementById(`${fieldId}-error`);
        if (errorElement) {
            errorElement.remove();
        }

        this.errors.delete(fieldId);
    }

    /**
     * Очищает все ошибки
     */
    clearAllErrors() {
        this.errors.forEach((errorElement) => {
            errorElement.remove();
        });
        this.errors.clear();

        // Убираем классы ошибок со всех полей
        document.querySelectorAll('.error').forEach((field) => {
            field.classList.remove('error');
            field.classList.remove('s21-input-focus', 's21-input-valid');
        });
    }

    /**
     * Валидация формы генерации контента
     */
    validateGenerationForm() {
        this.clearAllErrors();
        let isValid = true;

        // Валидация типа проекта (обязательное)
        const projectType = document.getElementById('projectType');
        if (!projectType || !projectType.value) {
            this.showError('projectType', 'Выберите тип проекта');
            isValid = false;
        }

        // Валидация размера группы (если групповой проект)
        if (projectType && projectType.value === 'group') {
            const groupSize = document.getElementById('groupSize');
            const groupSizeResult = this.validateNumber(groupSize?.value, 2, 10, 'Размер группы');
            if (!groupSizeResult.valid) {
                this.showError('groupSize', groupSizeResult.message);
                isValid = false;
            }
        }

        // Валидация тематического блока (обязательное)
        const thematicBlock = document.getElementById('thematicBlock');
        const thematicBlockResult = this.validateRequired(thematicBlock?.value, 'Тематический блок');
        if (!thematicBlockResult.valid) {
            this.showError('thematicBlock', thematicBlockResult.message);
            isValid = false;
        }

        // Валидация описания проекта (обязательное, минимум 10 символов)
        const projectDescription = document.getElementById('projectDescription');
        const projectDescriptionResult = this.validateLength(projectDescription?.value, 10, 5000, 'Описание проекта');
        if (!projectDescriptionResult.valid) {
            this.showError('projectDescription', projectDescriptionResult.message);
            isValid = false;
        }

        // Валидация образовательных результатов (обязательное, минимум 1)
        const learningOutcomes = document.getElementById('learningOutcomes');
        const learningOutcomesValue = learningOutcomes?.value?.trim();
        if (!learningOutcomesValue || learningOutcomesValue.split('\n').filter(l => l.trim()).length === 0) {
            this.showError('learningOutcomes', 'Укажите хотя бы один образовательный результат');
            isValid = false;
        }

        // Валидация навыков (обязательное, минимум 1)
        const skills = document.getElementById('skills');
        const skillsValue = skills?.value?.trim();
        if (!skillsValue || skillsValue.split('\n').filter(s => s.trim()).length === 0) {
            this.showError('skills', 'Укажите хотя бы один навык');
            isValid = false;
        }

        // Валидация количества частей теории (если указано)
        const theoryParts = document.getElementById('theoryParts');
        if (theoryParts && theoryParts.value) {
            const theoryPartsResult = this.validateNumber(theoryParts.value, 2, 6, 'Количество частей теории');
            if (!theoryPartsResult.valid) {
                this.showError('theoryParts', theoryPartsResult.message);
                isValid = false;
            }
        }

        // Валидация количества задач (если указано)
        const practiceTasks = document.getElementById('practiceTasks');
        if (practiceTasks && practiceTasks.value) {
            const practiceTasksResult = this.validateNumber(practiceTasks.value, 2, 8, 'Количество задач');
            if (!practiceTasksResult.valid) {
                this.showError('practiceTasks', practiceTasksResult.message);
                isValid = false;
            }
        }

        return isValid;
    }
}

// Создаем глобальный экземпляр
window.validator = new FormValidator();

/**
 * Инициализация валидации в реальном времени
 */
function initRealtimeValidation() {
    const validator = window.validator;
    if (!validator) return;
    
    // Список полей для валидации
    const fieldsToValidate = [
        { id: 'projectType', validate: (val) => val ? { valid: true } : { valid: false, message: 'Выберите тип проекта' } },
        { id: 'groupSize', validate: (val) => {
            const projectType = document.getElementById('projectType')?.value;
            if (projectType === 'group') {
                return validator.validateNumber(val, 2, 10, 'Размер группы');
            }
            return { valid: true };
        }},
        { id: 'thematicBlock', validate: (val) => validator.validateRequired(val, 'Тематический блок') },
        { id: 'projectDescription', validate: (val) => validator.validateLength(val, 10, 5000, 'Описание проекта') },
        { id: 'learningOutcomes', validate: (val) => {
            if (!val || val.trim().split('\n').filter(l => l.trim()).length === 0) {
                return { valid: false, message: 'Укажите хотя бы один образовательный результат' };
            }
            return { valid: true };
        }},
        { id: 'skills', validate: (val) => {
            if (!val || val.trim().split('\n').filter(s => s.trim()).length === 0) {
                return { valid: false, message: 'Укажите хотя бы один навык' };
            }
            return { valid: true };
        }},
        { id: 'theoryParts', validate: (val) => {
            if (val) {
                return validator.validateNumber(val, 2, 6, 'Количество частей теории');
            }
            return { valid: true };
        }},
        { id: 'practiceTasks', validate: (val) => {
            if (val) {
                return validator.validateNumber(val, 2, 8, 'Количество задач');
            }
            return { valid: true };
        }},
    ];
    
    // Добавляем обработчики для каждого поля
    fieldsToValidate.forEach(field => {
        const element = document.getElementById(field.id);
        if (!element) return;
        
        // Валидация при потере фокуса
        element.addEventListener('blur', () => {
            const value = element.value;
            const result = field.validate(value);
            if (!result.valid) {
                validator.showError(field.id, result.message);
            } else {
                validator.clearError(field.id);
                // Подсветка успешного заполнения
                element.classList.remove('s21-input-focus');
                element.classList.add('s21-input-valid');
                setTimeout(() => {
                    element.classList.remove('s21-input-valid');
                }, 2000);
            }
        });
        
        // Валидация при вводе (с задержкой)
        let timeout;
        element.addEventListener('input', () => {
            clearTimeout(timeout);
            timeout = setTimeout(() => {
                const value = element.value;
                const result = field.validate(value);
                if (!result.valid) {
                    validator.showError(field.id, result.message);
                } else {
                    validator.clearError(field.id);
                }
            }, 500);
        });
        
        // Подсветка при фокусе
        element.addEventListener('focus', () => {
            element.classList.add('s21-input-focus');
            element.classList.remove('s21-input-valid');
        });
    });
    
    // Специальная обработка для select элементов
    document.querySelectorAll('select').forEach(select => {
        select.addEventListener('change', () => {
            const field = fieldsToValidate.find(f => f.id === select.id);
            if (field) {
                const result = field.validate(select.value);
                if (!result.valid) {
                    validator.showError(select.id, result.message);
                } else {
                    validator.clearError(select.id);
                    select.classList.add('s21-input-valid');
                    setTimeout(() => {
                        select.classList.remove('s21-input-valid');
                    }, 2000);
                }
            }
        });
    });
}

// Инициализация при загрузке DOM
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initRealtimeValidation);
} else {
    initRealtimeValidation();
}

