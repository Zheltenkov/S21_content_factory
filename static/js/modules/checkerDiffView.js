// Checker README diff view helpers.
// Depends on checkerPage.js state getters and shared tab helpers.

        async function displayReadmeDiff() {
            const improvementRequestId = window.getCheckerImprovementRequestId ? window.getCheckerImprovementRequestId() : window.improvementRequestId;
            if (!improvementRequestId) {
                alert('Ошибка: request_id не найден');
                return;
            }
            
            try {
                const apiUrl = window.API_URL || (window.API_BASE ? `${window.API_BASE}/api/v1` : '/api/v1');
                const authHeaders = window.getAuthHeadersForImprovement();
                
                const response = await fetch(`${apiUrl}/readme/improve/diff/${improvementRequestId}`, {
                    headers: authHeaders
                });
                
                if (!response.ok) {
                    throw new Error(`Ошибка ${response.status}: ${response.statusText}`);
                }
                
                const diffData = await response.json();
                
                // Создаем новую вкладку для отображения diff
                const resultsArea = document.getElementById('resultsArea');
                if (!resultsArea) return;
                
                // Добавляем вкладку "Сравнение"
                const tabs = document.querySelector('.tabs');
                if (!tabs) {
                    console.error('❌ Контейнер .tabs не найден для добавления вкладки "Сравнение"');
                } else if (!document.getElementById('diffTab')) {
                    const diffTab = document.createElement('button');
                    diffTab.className = 'tab';
                    diffTab.id = 'diffTab';
                    diffTab.textContent = 'Сравнение';
                    diffTab.onclick = function() { 
                        if (typeof showTab === 'function') {
                            showTab('diff', this); 
                        } else {
                            // Fallback
                            document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
                            document.querySelectorAll('.tab-content').forEach(c => {
                                c.classList.remove('active');
                                c.style.display = 'none';
                            });
                            diffTab.classList.add('active');
                            const diffContent = document.getElementById('diff');
                            if (diffContent) {
                                diffContent.classList.add('active');
                                diffContent.style.display = 'block';
                            }
                        }
                    };
                    tabs.appendChild(diffTab);
                    console.log('✅ Вкладка "🔄 Сравнение" добавлена');
                } else {
                    console.log('ℹ️ Вкладка "🔄 Сравнение" уже существует');
                }
                
                // Создаем контент для diff
                let diffContent = document.getElementById('diff');
                if (!diffContent) {
                    diffContent = document.createElement('div');
                    diffContent.id = 'diff';
                    diffContent.className = 'tab-content';
                    diffContent.style.display = 'none'; // Явно скрываем
                } else {
                    // Убеждаемся, что контент скрыт, если он не активен
                    if (!diffContent.classList.contains('active')) {
                        diffContent.style.display = 'none';
                    }
                }
                
                // Получаем улучшенный README из window
                const improvedReadme = window.improvedReadme || '';
                const originalReadme = window.originalReadmeForImprovement || '';
                
                if (!improvedReadme) {
                    console.error('Улучшенный README не найден в window.improvedReadme');
                    // Пытаемся получить из diffData, если есть
                    if (diffData.improved_readme) {
                        window.improvedReadme = diffData.improved_readme;
                    } else {
                        alert('Улучшенный README не найден. Попробуйте обновить страницу.');
                        return;
                    }
                }
                
                // Отображаем статистику и контролы
                let html = '';
                html += '<div class="diff-container">';
                
                // Заголовок с статистикой
                if (diffData.stats) {
                    html += '<div class="diff-header">';
                    html += '<div class="diff-stats">';
                    html += '<h3>Статистика изменений</h3>';
                    html += `<div class="stats-grid">`;
                    html += `<div class="stat-item"><span class="stat-label">Исходный:</span> <span class="stat-value">${diffData.stats.original_lines} строк</span></div>`;
                    html += `<div class="stat-item"><span class="stat-label">Улучшенный:</span> <span class="stat-value">${diffData.stats.improved_lines} строк</span></div>`;
                    html += `<div class="stat-item stat-added"><span class="stat-label">Добавлено:</span> <span class="stat-value">${diffData.stats.added}</span></div>`;
                    html += `<div class="stat-item stat-deleted"><span class="stat-label">Удалено:</span> <span class="stat-value">${diffData.stats.deleted}</span></div>`;
                    html += `<div class="stat-item stat-modified"><span class="stat-label">Изменено:</span> <span class="stat-value">${diffData.stats.modified}</span></div>`;
                    html += `</div>`;
                    html += '</div>';
                }
                
                // Контролы фильтрации
                html += '<div class="diff-controls">';
                html += '<button class="diff-btn" id="showAllBtn" onclick="toggleDiffFilter(\'all\')">Весь документ</button>';
                html += '<button class="diff-btn active" id="showChangesBtn" onclick="toggleDiffFilter(\'changes\')">Только изменения</button>';
                html += '<button class="diff-btn" id="prevChangeBtn" onclick="navigateDiffChange(-1)" disabled>Предыдущее</button>';
                html += '<button class="diff-btn" id="nextChangeBtn" onclick="navigateDiffChange(1)" disabled>Следующее</button>';
                html += '</div>';
                html += '</div>';
                
                // Таблица diff
                html += '<div class="diff-table-wrapper">';
                html += '<table class="diff-table">';
                html += '<thead><tr>';
                html += '<th class="diff-header-cell">Строка</th>';
                html += '<th class="diff-header-cell">Исходный README</th>';
                html += '<th class="diff-header-cell">Строка</th>';
                html += '<th class="diff-header-cell">Улучшенный README</th>';
                html += '</tr></thead>';
                html += '<tbody>';
                
                // Используем side_by_side данные для построчного отображения
                const sideBySide = diffData.side_by_side || [];
                let originalLineNum = 0;
                let improvedLineNum = 0;
                let changeIndices = []; // Индексы строк с изменениями
                
                sideBySide.forEach((line, index) => {
                    const type = line.type || 'equal';
                    let rowClass = `diff-line diff-${type}`;
                    let originalLine = '';
                    let improvedLine = '';
                    let originalLineNumStr = '';
                    let improvedLineNumStr = '';
                    
                    if (type === 'equal') {
                        originalLineNum++;
                        improvedLineNum++;
                        originalLineNumStr = originalLineNum.toString();
                        improvedLineNumStr = improvedLineNum.toString();
                        originalLine = escapeHtml(line.original || '');
                        improvedLine = escapeHtml(line.improved || '');
                    } else if (type === 'delete') {
                        originalLineNum++;
                        originalLineNumStr = originalLineNum.toString();
                        improvedLineNumStr = '';
                        originalLine = escapeHtml(line.original || '');
                        improvedLine = '';
                        changeIndices.push(index);
                    } else if (type === 'insert') {
                        improvedLineNum++;
                        originalLineNumStr = '';
                        improvedLineNumStr = improvedLineNum.toString();
                        originalLine = '';
                        improvedLine = escapeHtml(line.improved || '');
                        changeIndices.push(index);
                    } else if (type === 'replace') {
                        if (line.original !== null && line.original !== undefined) {
                            originalLineNum++;
                            originalLineNumStr = originalLineNum.toString();
                        } else {
                            originalLineNumStr = '';
                        }
                        if (line.improved !== null && line.improved !== undefined) {
                            improvedLineNum++;
                            improvedLineNumStr = improvedLineNum.toString();
                        } else {
                            improvedLineNumStr = '';
                        }
                        originalLine = escapeHtml(line.original || '');
                        improvedLine = escapeHtml(line.improved || '');
                        changeIndices.push(index);
                    }
                    
                    html += `<tr class="${rowClass}" data-line-index="${index}">`;
                    html += `<td class="line-number original">${originalLineNumStr}</td>`;
                    html += `<td class="line-content original">${originalLine || '&nbsp;'}</td>`;
                    html += `<td class="line-number improved">${improvedLineNumStr}</td>`;
                    html += `<td class="line-content improved">${improvedLine || '&nbsp;'}</td>`;
                    html += '</tr>';
                });
                
                html += '</tbody></table>';
                html += '</div>';
                
                // Сохраняем индексы изменений для навигации
                window.diffChangeIndices = changeIndices;
                window.currentDiffChangeIndex = -1;
                
                // Обновляем состояние кнопок навигации
                if (changeIndices.length > 0) {
                    const nextBtn = document.getElementById('nextChangeBtn');
                    const prevBtn = document.getElementById('prevChangeBtn');
                    if (nextBtn) nextBtn.removeAttribute('disabled');
                    if (prevBtn) prevBtn.removeAttribute('disabled');
                }
                
                // Инициализируем фильтр "только изменения" по умолчанию
                setTimeout(() => {
                    toggleDiffFilter('changes');
                }, 100);
                
                // Убеждаемся, что контент скрыт перед обновлением (если он не активен)
                const wasActive = diffContent.classList.contains('active');
                if (!wasActive) {
                    diffContent.style.display = 'none';
                }
                
                diffContent.innerHTML = html;
                console.log('Diff контент установлен, длина HTML:', html.length);
                
                // Инициализируем фильтр "только изменения" по умолчанию после установки HTML
                setTimeout(() => {
                    if (typeof toggleDiffFilter === 'function') {
                        toggleDiffFilter('changes');
                    }
                }, 100);
                
                // Проверяем, добавлен ли контент в DOM
                const existingDiff = document.getElementById('diff');
                if (!existingDiff || !existingDiff.parentNode) {
                    // Добавляем после существующих tab-content элементов
                    const readmeContent = document.getElementById('readme');
                    if (readmeContent && readmeContent.parentNode) {
                        readmeContent.parentNode.insertBefore(diffContent, readmeContent.nextSibling);
                        console.log('Diff контент добавлен после readme');
                    } else {
                        // Ищем последний tab-content элемент
                        const lastTabContent = resultsArea.querySelector('.tab-content:last-of-type');
                        if (lastTabContent && lastTabContent.parentNode) {
                            lastTabContent.parentNode.insertBefore(diffContent, lastTabContent.nextSibling);
                            console.log('Diff контент добавлен после последнего tab-content');
                        } else {
                            resultsArea.appendChild(diffContent);
                            console.log('Diff контент добавлен в resultsArea');
                        }
                    }
                } else {
                    console.log('Diff контент уже существует в DOM, обновлен');
                }
                
                // Активируем вкладку "Сравнение"
                const diffTab = document.getElementById('diffTab');
                if (diffTab) {
                    console.log('Активируем вкладку Сравнение');
                    if (typeof showTab === 'function') {
                        showTab('diff', diffTab);
                    } else {
                        console.warn('showTab не доступна, активируем вручную');
                        // Fallback: активируем вручную
                        document.querySelectorAll('.tab').forEach(tab => {
                            tab.classList.remove('active');
                        });
                        document.querySelectorAll('.tab-content').forEach(content => {
                            content.classList.remove('active');
                            content.style.display = 'none'; // Явно скрываем все
                        });
                        diffTab.classList.add('active');
                        diffContent.classList.add('active');
                        diffContent.style.display = 'block'; // Явно показываем выбранную
                    }
                } else {
                    console.error('Вкладка diffTab не найдена');
                }
                
            } catch (error) {
                console.error('Ошибка получения diff:', error);
                // Если diff не получен, все равно показываем улучшенный README
                if (window.improvedReadme) {
                    console.log('Показываем улучшенный README без diff');
                    // Просто показываем улучшенный README без сравнения
                    const resultsArea = document.getElementById('resultsArea');
                    if (resultsArea) {
                        const diffContent = document.getElementById('diff');
                        if (diffContent) {
                            diffContent.innerHTML = '<div class="warnings s21-compact-warning"><p>Не удалось загрузить сравнение, но улучшенный README доступен во вкладке "Улучшенный README"</p></div>';
                        }
                    }
                } else {
                    alert(`Ошибка при получении сравнения: ${error.message}`);
                }
            }
        }
        
        function escapeHtml(text) {
            if (text === null || text === undefined) return '';
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
        
        // Функция переключения фильтра diff
        function toggleDiffFilter(filter) {
            const showAllBtn = document.getElementById('showAllBtn');
            const showChangesBtn = document.getElementById('showChangesBtn');
            const diffTable = document.querySelector('.diff-table tbody');
            
            if (!diffTable) return;
            
            // Обновляем активную кнопку
            if (showAllBtn && showChangesBtn) {
                showAllBtn.classList.remove('active');
                showChangesBtn.classList.remove('active');
                if (filter === 'all') {
                    showAllBtn.classList.add('active');
                } else {
                    showChangesBtn.classList.add('active');
                }
            }
            
            // Показываем/скрываем строки
            const rows = diffTable.querySelectorAll('tr.diff-line');
            rows.forEach((row, index) => {
                const type = row.className.match(/diff-(equal|delete|insert|replace)/)?.[1];
                if (filter === 'all') {
                    row.style.display = '';
                } else {
                    // Показываем только изменения и контекст вокруг них
                    if (type === 'equal') {
                        // Скрываем одинаковые строки, но показываем контекст (3 строки до и после изменений)
                        const lineIndex = parseInt(row.getAttribute('data-line-index') || '0');
                        const changeIndices = window.diffChangeIndices || [];
                        const hasNearbyChange = changeIndices.some(changeIdx => {
                            return Math.abs(changeIdx - lineIndex) <= 3;
                        });
                        row.style.display = hasNearbyChange ? '' : 'none';
                    } else {
                        row.style.display = '';
                    }
                }
            });
        }
        
        // Функция навигации по изменениям
        function navigateDiffChange(direction) {
            const changeIndices = window.diffChangeIndices || [];
            if (changeIndices.length === 0) return;
            
            let currentIndex = window.currentDiffChangeIndex || -1;
            currentIndex += direction;
            
            if (currentIndex < 0) {
                currentIndex = changeIndices.length - 1;
            } else if (currentIndex >= changeIndices.length) {
                currentIndex = 0;
            }
            
            window.currentDiffChangeIndex = currentIndex;
            const targetLineIndex = changeIndices[currentIndex];
            
            // Находим строку и прокручиваем к ней
            const targetRow = document.querySelector(`tr.diff-line[data-line-index="${targetLineIndex}"]`);
            if (targetRow) {
                targetRow.scrollIntoView({ behavior: 'smooth', block: 'center' });
                // Подсвечиваем строку временно
                targetRow.classList.add('diff-line-highlight');
                setTimeout(() => {
                    targetRow.classList.remove('diff-line-highlight');
                }, 2000);
            }
            
            // Обновляем состояние кнопок
            const prevBtn = document.getElementById('prevChangeBtn');
            const nextBtn = document.getElementById('nextChangeBtn');
            if (prevBtn && nextBtn) {
                prevBtn.removeAttribute('disabled');
                nextBtn.removeAttribute('disabled');
            }
        }
        


        window.displayReadmeDiff = displayReadmeDiff;
        window.toggleDiffFilter = toggleDiffFilter;
        window.navigateDiffChange = navigateDiffChange;
