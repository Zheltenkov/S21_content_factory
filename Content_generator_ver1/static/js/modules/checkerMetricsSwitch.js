// Checker criteria version switcher.
// Rendering of actual metric items is delegated to metricsView.js.

        // Функция переключения версий критериев (исходный/улучшенный)
        function switchCheckerMetricsVersion(version, clickedElement = null) {
            console.log('🔄 Переключение на версию:', version, {
                clickedElement: clickedElement,
                hasOriginal: !!window.checkerRubric,
                hasImproved: !!window.improvedRubric,
                originalItems: window.checkerRubric?.items?.length || 0,
                improvedItems: window.improvedRubric?.items?.length || 0
            });
            
            // Обновляем активные вкладки
            const tabOriginal = document.getElementById('checkerMetricsTabOriginal');
            const tabImproved = document.getElementById('checkerMetricsTabImproved');
            if (tabOriginal && tabImproved) {
                tabOriginal.classList.remove('active');
                tabImproved.classList.remove('active');
                if (version === 'original') {
                    tabOriginal.classList.add('active');
                    console.log('✅ Активирована вкладка: Исходный README');
                } else if (version === 'improved') {
                    tabImproved.classList.add('active');
                    console.log('✅ Активирована вкладка: Улучшенный README');
                }
            }
            
            // Получаем контейнеры для критериев
            const contentOriginal = document.getElementById('checkerMetricsOriginal');
            const contentImproved = document.getElementById('checkerMetricsImproved');
            
            // Проверяем доступность функции displayMetrics
            if (typeof window.displayMetrics !== 'function') {
                console.error('❌ window.displayMetrics не доступна');
                return;
            }
            
            // Вспомогательная функция для проверки валидности rubric
            function isValidRubric(rubric) {
                return rubric && 
                       rubric.items && 
                       Array.isArray(rubric.items) && 
                       rubric.items.length > 0;
            }
            
            // Небольшая задержка для гарантии обновления DOM перед отображением критериев
            requestAnimationFrame(() => {
                setTimeout(() => {
                    // Прямое переключение контента и отображение критериев
                    if (version === 'original' && window.checkerRubric) {
                        if (contentOriginal) contentOriginal.style.display = 'block';
                        if (contentImproved) contentImproved.style.display = 'none';
                        console.log('📊 Отображаем исходные критерии (прямое переключение)', {
                            itemsCount: window.checkerRubric?.items?.length || 0,
                            isValid: isValidRubric(window.checkerRubric)
                        });
                        if (isValidRubric(window.checkerRubric)) {
                            window.displayMetrics(window.checkerRubric, 'checkerMetricsOriginal');
                        } else {
                            console.error('❌ Исходные критерии невалидны');
                            if (contentOriginal) {
                                contentOriginal.innerHTML = '<div class="info-box">Исходные критерии недоступны</div>';
                            }
                        }
                    } else if (version === 'improved' && window.improvedRubric) {
                        if (contentOriginal) {
                            contentOriginal.style.display = 'none';
                            contentOriginal.classList.remove('active');
                        }
                        if (contentImproved) {
                            contentImproved.style.display = 'block';
                            contentImproved.classList.add('active');
                        }
                        console.log('✨ Отображаем улучшенные критерии (прямое переключение)', {
                            itemsCount: window.improvedRubric?.items?.length || 0,
                            hasItems: !!window.improvedRubric?.items,
                            itemsIsArray: Array.isArray(window.improvedRubric?.items),
                            isValid: isValidRubric(window.improvedRubric),
                            rubricKeys: window.improvedRubric ? Object.keys(window.improvedRubric) : null,
                            contentImprovedDisplay: contentImproved?.style.display,
                            contentImprovedActive: contentImproved?.classList.contains('active')
                        });
                        // КРИТИЧЕСКИ ВАЖНО: Проверяем структуру rubric перед отображением
                        if (isValidRubric(window.improvedRubric)) {
                            // Небольшая задержка для гарантии обновления DOM
                            setTimeout(() => {
                                window.displayMetrics(window.improvedRubric, 'checkerMetricsImproved');
                                console.log('✅ displayMetrics вызвана для улучшенных критериев');
                            }, 50);
                        } else {
                            console.error('❌ Улучшенные критерии невалидны:', {
                                hasRubric: !!window.improvedRubric,
                                hasItems: !!window.improvedRubric?.items,
                                itemsType: typeof window.improvedRubric?.items,
                                itemsIsArray: Array.isArray(window.improvedRubric?.items),
                                itemsLength: window.improvedRubric?.items?.length || 0,
                                rubricKeys: window.improvedRubric ? Object.keys(window.improvedRubric) : null
                            });
                            if (contentImproved) {
                                contentImproved.innerHTML = '<div class="info-box">Улучшенные критерии недоступны (данные отсутствуют или невалидны)</div>';
                            }
                        }
                    } else {
                        // Fallback: вызываем updateCheckerMetricsDisplay
                        console.log('⚠️ Прямое переключение не удалось, используем updateCheckerMetricsDisplay');
                        updateCheckerMetricsDisplay();
                    }
                }, 100);
            });
        }
        
        // Функция обновления отображения критериев
        function updateCheckerMetricsDisplay() {
            const originalRubric = window.checkerRubric || null;
            const improvedRubric = window.improvedRubric || null;
            
            console.log('🔄 updateCheckerMetricsDisplay вызвана', {
                hasOriginal: !!originalRubric,
                hasImproved: !!improvedRubric,
                originalItems: originalRubric?.items?.length || 0,
                improvedItems: improvedRubric?.items?.length || 0,
                originalRubricType: typeof originalRubric,
                improvedRubricType: typeof improvedRubric,
                originalIsValid: originalRubric && Array.isArray(originalRubric.items) && originalRubric.items.length > 0,
                improvedIsValid: improvedRubric && Array.isArray(improvedRubric.items) && improvedRubric.items.length > 0
            });
            
            // Показываем переключатель, если есть обе версии
            const switcher = document.getElementById('checkerMetricsVersionSwitcher');
            if (switcher) {
                if (originalRubric && improvedRubric) {
                    switcher.style.display = 'block';
                    console.log('✅ Переключатель критериев показан');
                } else {
                    switcher.style.display = 'none';
                }
            }
            
            // Получаем элементы
            const tabOriginal = document.getElementById('checkerMetricsTabOriginal');
            const tabImproved = document.getElementById('checkerMetricsTabImproved');
            const contentOriginal = document.getElementById('checkerMetricsOriginal');
            const contentImproved = document.getElementById('checkerMetricsImproved');
            
            if (!contentOriginal || !contentImproved) {
                console.warn('⚠️ Контейнеры критериев не найдены');
                return;
            }
            
            // Определяем активную версию (проверяем обе вкладки)
            // Сначала проверяем явно установленное состояние вкладок
            let activeVersion = null;
            
            // Проверяем tabImproved ПЕРВЫМ, так как он может быть активен при переключении
            if (tabImproved && tabImproved.classList.contains('active')) {
                activeVersion = 'improved';
                console.log('📌 Активная версия определена из tabImproved: improved');
            } else if (tabOriginal && tabOriginal.classList.contains('active')) {
                activeVersion = 'original';
                console.log('📌 Активная версия определена из tabOriginal: original');
            }
            
            // Если ни одна вкладка не активна, определяем по наличию данных
            if (!activeVersion) {
                if (improvedRubric && !originalRubric) {
                    activeVersion = 'improved';
                    // Активируем соответствующую вкладку
                    if (tabImproved) tabImproved.classList.add('active');
                    if (tabOriginal) tabOriginal.classList.remove('active');
                    console.log('📌 Активная версия определена по данным: improved (только улучшенные)');
                } else if (originalRubric) {
                    activeVersion = 'original';
                    // Активируем соответствующую вкладку
                    if (tabOriginal) tabOriginal.classList.add('active');
                    if (tabImproved) tabImproved.classList.remove('active');
                    console.log('📌 Активная версия определена по данным: original (по умолчанию)');
                } else {
                    // Если нет ни одной версии, используем original как fallback
                    activeVersion = 'original';
                    if (tabOriginal) tabOriginal.classList.add('active');
                    if (tabImproved) tabImproved.classList.remove('active');
                    console.log('📌 Активная версия определена как fallback: original');
                }
            }
            
            console.log('📌 Активная версия:', activeVersion, {
                tabOriginalActive: tabOriginal?.classList.contains('active'),
                tabImprovedActive: tabImproved?.classList.contains('active'),
                hasOriginal: !!originalRubric,
                hasImproved: !!improvedRubric,
                tabOriginalExists: !!tabOriginal,
                tabImprovedExists: !!tabImproved
            });
            
            // Проверяем доступность функции displayMetrics
            if (typeof window.displayMetrics !== 'function') {
                console.error('❌ window.displayMetrics не доступна');
                return;
            }
            
            // Вспомогательная функция для проверки валидности rubric
            function isValidRubric(rubric) {
                return rubric && 
                       rubric.items && 
                       Array.isArray(rubric.items) && 
                       rubric.items.length > 0;
            }
            
            // Показываем/скрываем контейнеры и отображаем соответствующие критерии
            if (originalRubric && improvedRubric) {
                // Есть обе версии - показываем в зависимости от активной вкладки
                if (activeVersion === 'original') {
                    contentOriginal.style.display = 'block';
                    contentImproved.style.display = 'none';
                    console.log('📊 Отображаем исходные критерии', {
                        itemsCount: originalRubric?.items?.length || 0,
                        isValid: isValidRubric(originalRubric)
                    });
                    if (isValidRubric(originalRubric)) {
                        window.displayMetrics(originalRubric, 'checkerMetricsOriginal');
                    } else {
                        console.warn('⚠️ Исходные критерии пусты или невалидны');
                        contentOriginal.innerHTML = '<div class="info-box">Исходные критерии недоступны</div>';
                    }
                } else if (activeVersion === 'improved') {
                    contentOriginal.style.display = 'none';
                    contentImproved.style.display = 'block';
                    console.log('✨ Отображаем улучшенные критерии', {
                        rubric: improvedRubric,
                        itemsCount: improvedRubric?.items?.length || 0,
                        hasItems: !!improvedRubric?.items,
                        itemsIsArray: Array.isArray(improvedRubric?.items),
                        activeVersion: activeVersion,
                        isValid: isValidRubric(improvedRubric)
                    });
                    // КРИТИЧЕСКИ ВАЖНО: Проверяем структуру rubric перед отображением
                    if (isValidRubric(improvedRubric)) {
                        window.displayMetrics(improvedRubric, 'checkerMetricsImproved');
                    } else {
                        console.error('❌ Улучшенные критерии невалидны:', {
                            hasRubric: !!improvedRubric,
                            hasItems: !!improvedRubric?.items,
                            itemsType: typeof improvedRubric?.items,
                            itemsIsArray: Array.isArray(improvedRubric?.items),
                            itemsLength: improvedRubric?.items?.length || 0,
                            rubricKeys: improvedRubric ? Object.keys(improvedRubric) : null,
                            rubricValue: improvedRubric
                        });
                        contentImproved.innerHTML = '<div class="info-box">Улучшенные критерии недоступны (данные отсутствуют или невалидны)</div>';
                    }
                } else {
                    // Fallback: если activeVersion не определен, показываем исходные
                    console.warn('⚠️ activeVersion не определен, показываем исходные критерии');
                    contentOriginal.style.display = 'block';
                    contentImproved.style.display = 'none';
                    if (isValidRubric(originalRubric)) {
                        window.displayMetrics(originalRubric, 'checkerMetricsOriginal');
                    } else {
                        contentOriginal.innerHTML = '<div class="info-box">Исходные критерии недоступны</div>';
                    }
                }
            } else if (originalRubric) {
                // Только исходные критерии - показываем их
                contentOriginal.style.display = 'block';
                contentImproved.style.display = 'none';
                console.log('📊 Отображаем только исходные критерии');
                if (isValidRubric(originalRubric)) {
                    window.displayMetrics(originalRubric, 'checkerMetricsOriginal');
                } else {
                    contentOriginal.innerHTML = '<div class="info-box">Исходные критерии недоступны</div>';
                }
            } else if (improvedRubric) {
                // Только улучшенные критерии - показываем их
                contentOriginal.style.display = 'none';
                contentImproved.style.display = 'block';
                console.log('✨ Отображаем только улучшенные критерии', {
                    rubric: improvedRubric,
                    itemsCount: improvedRubric?.items?.length || 0,
                    hasItems: !!improvedRubric?.items,
                    itemsIsArray: Array.isArray(improvedRubric?.items),
                    isValid: isValidRubric(improvedRubric)
                });
                // КРИТИЧЕСКИ ВАЖНО: Проверяем структуру rubric перед отображением
                if (isValidRubric(improvedRubric)) {
                    window.displayMetrics(improvedRubric, 'checkerMetricsImproved');
                } else {
                    console.error('❌ Улучшенные критерии невалидны:', {
                        hasRubric: !!improvedRubric,
                        hasItems: !!improvedRubric?.items,
                        itemsType: typeof improvedRubric?.items,
                        itemsIsArray: Array.isArray(improvedRubric?.items),
                        itemsLength: improvedRubric?.items?.length || 0,
                        rubricKeys: improvedRubric ? Object.keys(improvedRubric) : null,
                        rubricValue: improvedRubric
                    });
                    contentImproved.innerHTML = '<div class="info-box">Улучшенные критерии недоступны (данные отсутствуют или невалидны)</div>';
                }
            } else {
                console.warn('⚠️ Нет критериев для отображения');
            }
        }
        


        window.switchCheckerMetricsVersion = switchCheckerMetricsVersion;
        window.updateCheckerMetricsDisplay = updateCheckerMetricsDisplay;
