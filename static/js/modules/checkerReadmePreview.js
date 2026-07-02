// Checker improved README preview and download helpers.
// Loaded before checkerPage.js; runtime state is read through window getters.

        function displayImprovedReadme(markdown) {
            if (!markdown) {
                console.error('Улучшенный README пуст');
                return;
            }
            
            console.log('displayImprovedReadme вызвана, длина markdown:', markdown.length);
            
            const resultsArea = document.getElementById('resultsArea');
            if (!resultsArea) {
                console.error('resultsArea не найден');
                return;
            }
            
            // Добавляем вкладку "Улучшенный README"
            const tabs = document.querySelector('.tabs');
            if (!tabs) {
                console.error('tabs не найден');
                return;
            }
            
            if (!document.getElementById('improvedReadmeTab')) {
                const improvedTab = document.createElement('button');
                improvedTab.className = 'tab';
                improvedTab.id = 'improvedReadmeTab';
                improvedTab.textContent = 'Улучшенный README';
                improvedTab.onclick = function() { 
                    console.log('Клик по вкладке улучшенного README');
                    showTab('improvedReadme', this); 
                };
                tabs.appendChild(improvedTab);
                console.log('Вкладка "Улучшенный README" добавлена');
            }
            
            // Создаем контент для улучшенного README
            let improvedContent = document.getElementById('improvedReadme');
            if (!improvedContent) {
                improvedContent = document.createElement('div');
                improvedContent.id = 'improvedReadme';
                improvedContent.className = 'tab-content';
                improvedContent.style.display = 'none'; // Явно скрываем
                // Добавляем после существующих tab-content элементов
                const readmeContent = document.getElementById('readme');
                if (readmeContent && readmeContent.parentNode) {
                    readmeContent.parentNode.insertBefore(improvedContent, readmeContent.nextSibling);
                } else {
                    // Ищем последний tab-content элемент
                    const lastTabContent = resultsArea.querySelector('.tab-content:last-of-type');
                    if (lastTabContent && lastTabContent.parentNode) {
                        lastTabContent.parentNode.insertBefore(improvedContent, lastTabContent.nextSibling);
                    } else {
                        resultsArea.appendChild(improvedContent);
                    }
                }
                console.log('Контент для улучшенного README создан и добавлен в DOM');
            } else {
                // Убеждаемся, что контент скрыт, если он не активен
                if (!improvedContent.classList.contains('active')) {
                    improvedContent.style.display = 'none';
                }
                // Проверяем, что контент находится в DOM
                if (!improvedContent.parentNode) {
                    const readmeContent = document.getElementById('readme');
                    if (readmeContent && readmeContent.parentNode) {
                        readmeContent.parentNode.insertBefore(improvedContent, readmeContent.nextSibling);
                        console.log('Контент для улучшенного README передобавлен в DOM');
                    }
                }
            }
            
            // Сохраняем текущую позицию скролла
            const scrollPosition = window.scrollY || document.documentElement.scrollTop;
            
            // Создаем контейнер для markdown preview (без критериев - они отображаются во вкладке "Критерии")
            improvedContent.innerHTML = '<div id="improvedReadmePreview" class="markdown-preview result-markdown"></div><div class="s21-preview-actions"><button class="btn" onclick="downloadImprovedReadme()">📥 Скачать улучшенный README</button></div>';
            
            // Используем displayMarkdown из main.js для правильной обработки формул MathJax
            if (typeof window.displayMarkdown === 'function') {
                console.log('Используем displayMarkdown для отображения улучшенного README');
                window.displayMarkdown(markdown, 'improvedReadmePreview');
                
                // После отображения markdown заменяем изображения на base64
                setTimeout(() => {
                    hydrateImprovedReadmeImages('improvedReadmePreview');
                    
                    // Восстанавливаем позицию скролла после рендеринга (с задержкой для MathJax)
                    setTimeout(() => {
                        if (scrollPosition > 0) {
                            window.scrollTo({ top: scrollPosition, behavior: 'instant' });
                        }
                    }, 300);
                }, 100);
            } else {
                console.warn('displayMarkdown не доступна, используем простой рендеринг');
                // Fallback: используем marked напрямую
                const previewDiv = document.getElementById('improvedReadmePreview');
                if (previewDiv && typeof marked !== 'undefined') {
                    try {
                        previewDiv.innerHTML = marked.parse(markdown);
                        // Пытаемся вызвать MathJax, если доступен
                        if (window.MathJax && typeof window.MathJax.typesetPromise === 'function') {
                            window.MathJax.typesetPromise([previewDiv]).catch(err => console.error('MathJax error:', err));
                        }
                        // Заменяем изображения на base64
                        setTimeout(() => {
                            hydrateImprovedReadmeImages('improvedReadmePreview');
                        }, 100);
                    } catch (e) {
                        console.error('Ошибка парсинга markdown:', e);
                        previewDiv.innerHTML = '<pre class="s21-raw-preview">' + escapeHtml(markdown) + '</pre>';
                    }
                }
            }
            
            // Убеждаемся, что контент скрыт перед обновлением (если он не активен)
            const wasActive = improvedContent.classList.contains('active');
            if (!wasActive) {
                improvedContent.style.display = 'none';
            }
            console.log('Контент улучшенного README установлен');
            
            // Активируем вкладку "Улучшенный README"
            const improvedTab = document.getElementById('improvedReadmeTab');
            if (improvedTab) {
                improvedTab.style.display = 'inline-flex';
                console.log('Активируем вкладку улучшенного README');
                if (typeof showTab === 'function') {
                    // Используем showTab, которая правильно скроет все остальные вкладки
                    showTab('improvedReadme', improvedTab);
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
                    improvedTab.classList.add('active');
                    improvedContent.classList.add('active');
                    improvedContent.style.display = 'block'; // Явно показываем выбранную
                }
            } else {
                console.error('Вкладка improvedReadmeTab не найдена');
            }
        }
        
        function hydrateImprovedReadmeImages(containerId) {
            const container = document.getElementById(containerId);
            if (!container) {
                return;
            }
            
            // Получаем assets из window или из результата генерации
            let assets = window.improvedReadmeAssets;
            if (!assets) {
                // Пытаемся получить из текущего результата, если доступен
                if (typeof window.currentResult !== 'undefined' && window.currentResult && window.currentResult.assets) {
                    assets = window.currentResult.assets;
                } else {
                    console.warn('Assets для улучшенного README не найдены');
                    return;
                }
            }
            
            const map = new Map();
            
            // Обрабатываем изображения из assets.images
            const images = Array.isArray(assets.images) ? assets.images : [];
            images.forEach(img => {
                if (img && img.name && img.data) {
                    map.set(img.name, img.data);
                }
            });
            
            // Обрабатываем изображения из assets.files
            const files = Array.isArray(assets.files) ? assets.files : [];
            files.forEach(file => {
                const path = file && (file.path || file.name);
                if (!path || !file.data) {
                    return;
                }
                const name = path.split('/').pop();
                // Проверяем, что это изображение
                if (name && (name.endsWith('.png') || name.endsWith('.jpg') || name.endsWith('.jpeg') || name.endsWith('.gif'))) {
                    if (!map.has(name)) {
                        map.set(name, file.data);
                    }
                }
            });
            
            if (!map.size) {
                console.warn('Не найдено изображений в assets');
                return;
            }
            
            // Заменяем пути к изображениям на base64 data URLs
            const imgNodes = container.querySelectorAll('img');
            imgNodes.forEach(img => {
                const src = img.getAttribute('src');
                if (!src) {
                    return;
                }
                
                // Обрабатываем пути вида /app/images/diagram_1.png или images/diagram_1.png
                let name = null;
                if (src.startsWith('/app/images/')) {
                    name = src.replace('/app/images/', '');
                } else if (src.startsWith('images/')) {
                    name = src.replace('images/', '');
                } else if (src.startsWith('/images/')) {
                    name = src.replace('/images/', '');
                }
                
                if (name) {
                    const base64 = map.get(name);
                    if (base64) {
                        // Определяем тип изображения по расширению
                        let mimeType = 'image/png';
                        if (name.endsWith('.jpg') || name.endsWith('.jpeg')) {
                            mimeType = 'image/jpeg';
                        } else if (name.endsWith('.gif')) {
                            mimeType = 'image/gif';
                        }
                        img.src = `data:${mimeType};base64,${base64}`;
                        console.log(`✅ Изображение заменено: ${name}`);
                    } else {
                        console.warn(`⚠️ Изображение не найдено в assets: ${name}`);
                    }
                }
            });
        }
        
        async function downloadImprovedReadme() {
            const improvementGenerationRequestId = window.getCheckerImprovementGenerationRequestId ? window.getCheckerImprovementGenerationRequestId() : window.improvementGenerationRequestId;
            if (!improvementGenerationRequestId) {
                alert('Ошибка: ID запроса генерации не найден. Пожалуйста, дождитесь завершения генерации.');
                return;
            }
            
            try {
                const apiUrl = window.API_URL || (window.API_BASE ? `${window.API_BASE}/api/v1` : '/api/v1');
                const authHeaders = window.getAuthHeadersForImprovement();
                
                if (!authHeaders['Authorization']) {
                    alert('Ошибка: требуется авторизация. Пожалуйста, войдите в систему.');
                    return;
                }
                
                const response = await fetch(`${apiUrl}/readme/improve/download/${improvementGenerationRequestId}`, {
                    headers: authHeaders
                });
                
                if (!response.ok) {
                    const error = await response.json().catch(() => ({ detail: 'Ошибка скачивания' }));
                    throw new Error(error.detail || `Ошибка ${response.status}`);
                }
                
                // Получаем имя файла из заголовка Content-Disposition
                const disposition = response.headers.get('Content-Disposition') || '';
                const fileNameMatch = disposition.match(/filename="?([^"]+)"?/i);
                const fileName = fileNameMatch ? fileNameMatch[1] : `regen_README_${improvementGenerationRequestId}.zip`;
                
                const blob = await response.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = fileName;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                URL.revokeObjectURL(url);
                
                console.log('✅ Улучшенный README с архивом скачан:', fileName);
            } catch (error) {
                console.error('Ошибка при скачивании улучшенного README:', error);
                alert(`Ошибка при скачивании: ${error.message}`);
            }
        }
        


        window.displayImprovedReadme = displayImprovedReadme;
        window.hydrateImprovedReadmeImages = hydrateImprovedReadmeImages;
        window.downloadImprovedReadme = downloadImprovedReadme;
