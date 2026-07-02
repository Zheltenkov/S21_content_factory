// Shared Markdown normalization/rendering utilities for generator, checker and translator.
// Keep this file API-compatible with the legacy globals previously declared in main.js.

        const BLOCK_PLACEHOLDER = (idx) => `@@FORMULABLOCK${idx}@@`;
        const INLINE_PLACEHOLDER = (idx) => `@@FORMULAINLINE${idx}@@`;
        const escapeRegex = (str) => str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const MARKDOWN_VENDOR_ASSETS = {
            marked: '/static/vendor/marked/marked.min.js?v=20260518-local',
            mermaid: '/static/vendor/mermaid/mermaid.min.js?v=20260518-local',
            mathjax: '/static/vendor/mathjax/tex-mml-chtml.js?v=20260518-local',
        };
        const vendorLoadPromises = {};
        let markdownRenderSequence = 0;

        function beginMarkdownRender(container) {
            const token = `markdown-render-${Date.now()}-${++markdownRenderSequence}`;
            if (container?.dataset) {
                container.dataset.markdownRenderToken = token;
            }
            return token;
        }

        function isMarkdownRenderCurrent(container, token) {
            if (!container || !container.isConnected) return false;
            if (!token) return true;
            return container.dataset?.markdownRenderToken === token;
        }

        function isDetachedDomError(err) {
            const message = String(err?.message || err || '');
            return /replaceChild|insertBefore|removeChild|appendChild|Cannot read properties of null|Node was not found/i.test(message);
        }

        function ignoreStaleRenderError(err, container, token, source) {
            if (!isMarkdownRenderCurrent(container, token) || isDetachedDomError(err)) {
                console.debug(`[${source}] Игнорируем устаревший DOM-рендер Markdown:`, err);
                return true;
            }
            return false;
        }

        function loadScriptOnce(key, src, isReady) {
            if (typeof window === 'undefined' || isReady()) {
                return Promise.resolve();
            }
            if (vendorLoadPromises[key]) {
                return vendorLoadPromises[key];
            }

            vendorLoadPromises[key] = new Promise((resolve, reject) => {
                const existing = document.querySelector(`script[data-contentgen-vendor="${key}"]`);
                if (existing) {
                    existing.addEventListener('load', () => resolve(), { once: true });
                    existing.addEventListener('error', () => reject(new Error(`Не удалось загрузить ${key}`)), { once: true });
                    return;
                }

                const script = document.createElement('script');
                script.src = src;
                script.async = true;
                script.dataset.contentgenVendor = key;
                script.onload = () => resolve();
                script.onerror = () => reject(new Error(`Не удалось загрузить ${key}`));
                document.head.appendChild(script);
            });

            return vendorLoadPromises[key];
        }

        function ensureMarkedLoaded() {
            return loadScriptOnce('marked', MARKDOWN_VENDOR_ASSETS.marked, () => typeof window.marked !== 'undefined');
        }

        function ensureMermaidLoaded() {
            return loadScriptOnce('mermaid', MARKDOWN_VENDOR_ASSETS.mermaid, () => typeof window.mermaid !== 'undefined')
                .then(() => {
                    if (window.mermaid && !window.__contentGenMermaidInitialized) {
                        window.mermaid.initialize({
                            startOnLoad: false,
                            securityLevel: 'loose',
                            theme: 'base',
                            themeVariables: {
                                primaryColor: '#ffffff',
                                primaryTextColor: '#0f1419',
                                primaryBorderColor: '#c8cec4',
                                lineColor: '#0f1419',
                                secondaryColor: '#f7f7f5',
                                tertiaryColor: '#f3f3f0',
                                background: '#ffffff',
                                mainBkg: '#ffffff',
                                secondBkg: '#eef4ef',
                                textColor: '#0f1419',
                                actorBkg: '#ffffff',
                                actorBorder: '#9aa79d',
                                actorTextColor: '#0f1419',
                                actorLineColor: '#334238',
                                signalColor: '#334238',
                                signalTextColor: '#0f1419',
                                labelBoxBkgColor: '#ffffff',
                                labelBoxBorderColor: '#9aa79d',
                                noteBkgColor: '#f7faf6',
                                noteTextColor: '#0f1419',
                                activationBkgColor: '#eef4ef',
                                activationBorderColor: '#9aa79d',
                                fontFamily: 'Inter, Arial, sans-serif',
                                fontSize: '14px',
                            },
                            flowchart: {
                                htmlLabels: true,
                                curve: 'basis',
                                padding: 12,
                                nodeSpacing: 36,
                                rankSpacing: 42,
                                useMaxWidth: true,
                            },
                        });
                        window.__contentGenMermaidInitialized = true;
                    }
                });
        }

        function ensureMathJaxLoaded() {
            return loadScriptOnce('mathjax', MARKDOWN_VENDOR_ASSETS.mathjax, () => {
                return !!(window.MathJax && window.MathJax.startup && window.MathJax.startup.promise);
            });
        }

        function rootHasMath(root) {
            const text = root?.textContent || '';
            return /\$\$|(^|[^\\])\$[^$\n]+\$|\\\(|\\\[/.test(text);
        }

        function byId(id) {
            return document.getElementById(id);
        }

        function getChecked(id, fallback = false) {
            const element = byId(id);
            return element ? !!element.checked : fallback;
        }

        function setChecked(id, value) {
            const element = byId(id);
            if (element) {
                element.checked = !!value;
            }
        }

        function setValue(id, value) {
            const element = byId(id);
            if (element) {
                element.value = value ?? '';
            }
        }

        function normalizeAudienceLevel(value) {
            const norm = String(value || '').trim().toLowerCase();
            if (!norm) return 'beginner_plus';
            if (['beginner_plus', 'beginner+', 'basic+', 'base+', 'базовый+', 'начальный+'].includes(norm)) {
                return 'beginner_plus';
            }
            if (['beginner', 'basic', 'base', 'базовый', 'начальный'].includes(norm)) return 'beginner';
            if (['middle', 'intermediate', 'средний'].includes(norm)) return 'middle';
            if (['advanced', 'продвинутый'].includes(norm)) return 'advanced';
            if (['professional', 'pro', 'expert', 'профессиональный', 'экспертный'].includes(norm)) return 'professional';
            return 'beginner_plus';
        }

        function setAudienceLevel(value) {
            setValue('audienceLevel', normalizeAudienceLevel(value));
        }

        function splitCommaList(value) {
            return String(value || '')
                .split(',')
                .map((item) => item.trim())
                .filter(Boolean);
        }

        function setDisplay(id, displayValue) {
            const element = byId(id);
            if (element) {
                element.style.display = displayValue;
            }
        }

        function protectFormulas(markdown) {
            const block = [];
            const inline = [];
            let text = markdown.replace(/\$\$([\s\S]*?)\$\$/g, (match, formula) => {
                const idx = block.length;
                block.push(formula.trim());
                return BLOCK_PLACEHOLDER(idx);
            });

            const inlineRegex = /(^|[^\$\\])\$(?!\$)([^$\n]+?)\$(?!\$)/g;
            text = text.replace(inlineRegex, (match, prefix, formula) => {
                const idx = inline.length;
                inline.push(formula.trim());
                return `${prefix}${INLINE_PLACEHOLDER(idx)}`;
            });

            return { markdown: text, block, inline };
        }

        function restoreFormulas(html, guards) {
            const { block, inline } = guards;

            block.forEach((formula, index) => {
                const placeholder = new RegExp(escapeRegex(BLOCK_PLACEHOLDER(index)), 'g');
                html = html.replace(placeholder, `\n\n$$${formula}$$\n\n`);
            });

            inline.forEach((formula, index) => {
                const placeholder = new RegExp(escapeRegex(INLINE_PLACEHOLDER(index)), 'g');
                html = html.replace(placeholder, `$${formula}$`);
            });

            return html;
        }

        function applyOutsideFencedBlocks(markdown, transform) {
            const text = markdown || '';
            const rx = /```[\s\S]*?```/g;
            let result = '';
            let pos = 0;
            let match;
            while ((match = rx.exec(text)) !== null) {
                result += transform(text.slice(pos, match.index));
                result += match[0];
                pos = match.index + match[0].length;
            }
            result += transform(text.slice(pos));
            return result;
        }

        function looksLikeFlattenedTable(line) {
            const pipeCount = (line.match(/\|/g) || []).length;
            return pipeCount >= 6
                && /\|\s+(?=\|)/.test(line)
                && /\|\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|/.test(line);
        }

        function repairFlattenedTableLine(line) {
            const firstPipe = line.indexOf('|');
            if (firstPipe < 0) return [line];

            const prefix = line.slice(0, firstPipe).trimEnd();
            const tableText = line.slice(firstPipe).trim().replace(/\|\s+(?=\|)/g, '|\n');
            const repaired = [];
            const trailing = [];

            if (prefix) repaired.push(prefix, '');

            tableText.split(/\r?\n/).forEach((rawRow) => {
                const row = rawRow.trimEnd();
                if (!row) return;
                if (!row.trimStart().startsWith('|')) {
                    trailing.push(row.trim());
                    return;
                }

                const lastPipe = row.lastIndexOf('|');
                if (lastPipe <= 0) {
                    repaired.push(row);
                    return;
                }

                const core = row.slice(0, lastPipe + 1).trimEnd();
                const suffix = row.slice(lastPipe + 1).trim();
                if (core) repaired.push(core);
                if (suffix) trailing.push(suffix);
            });

            if (trailing.length) repaired.push('', ...trailing);
            return repaired.length ? repaired : [line];
        }

        function normalizeInlineMarkdownTables(markdown) {
            return applyOutsideFencedBlocks(markdown || '', (chunk) => {
                const output = [];
                chunk.split(/\r?\n/).forEach((line) => {
                    if (looksLikeFlattenedTable(line)) {
                        output.push(...repairFlattenedTableLine(line));
                    } else {
                        output.push(line);
                    }
                });
                return output.join('\n');
            });
        }

        function normalizeMermaidArrowSyntax(code) {
            return String(code || '')
                .replace(/\s*(?:[\u2013\u2014\u2212]+\s*>|[-\u2013\u2014\u2212]?\s*\u2192)\s*/g, ' --> ')
                .replace(/\s*\u21d2\s*/g, ' ==> ')
                .replace(/(^|[^-.])-\s*>(?!>)/g, '$1 --> ')
                .replace(/--\s+>/g, '-->')
                .replace(/==\s+>/g, '==>')
                .replace(/-\.\s+>/g, '-.->')
                .replace(/(-->|==>|-\.->)\s+\|/g, '$1|');
        }

        function normalizeSequenceMermaidStatements(code) {
            let text = String(code || '');
            if (!/^\s*sequenceDiagram\b/i.test(text)) return text;

            const sequenceArrow = '(?:-{1,2}|={1,2})(?:>>|>|x|\\))[+x-]?';
            text = text.replace(/\bsequenceDiagram\b\s*(?=\S)/i, 'sequenceDiagram\n    ');
            text = text.replace(/\s+(?=(?:participant|actor)\s+[A-Za-z][A-Za-z0-9_]*\b)/gi, '\n    ');
            text = text.replace(/\s+(?=Note\s+(?:over|left of|right of)\b)/gi, '\n    ');
            text = text.replace(/\s+(?=(?:alt|else|opt|loop|par|and|critical|break|end)\b)/gi, '\n    ');
            text = text.replace(
                new RegExp(`\\s+(?=[A-Za-z][A-Za-z0-9_]*\\s*${sequenceArrow}\\s*[A-Za-z][A-Za-z0-9_]*\\s*:)`, 'g'),
                '\n    '
            );
            return repairSequenceLeadingAlias(text, sequenceArrow);
        }

        function repairSequenceLeadingAlias(text, sequenceArrow) {
            const lines = String(text || '').split(/\r?\n/);
            const declarationIndex = lines.findIndex((line) => /^\s*sequenceDiagram\b/i.test(line));
            if (declarationIndex < 0) return text;

            const statementIndex = lines.findIndex((line, index) => index > declarationIndex && line.trim());
            if (statementIndex < 0) return text;

            const candidate = lines[statementIndex].trim();
            const sequenceStatement = new RegExp(
                `^(?:participant\\b|actor\\b|autonumber\\b|activate\\b|deactivate\\b|destroy\\b|rect\\b|opt\\b|alt\\b|else\\b|loop\\b|par\\b|and\\b|critical\\b|break\\b|end\\b|Note\\s+(?:over|left of|right of)\\b|[A-Za-z][A-Za-z0-9_]*\\s*${sequenceArrow}\\s*[A-Za-z][A-Za-z0-9_]*\\s*:)`,
                'i'
            );
            if (
                !candidate
                || candidate.length > 80
                || sequenceStatement.test(candidate)
                || /->|--|=>|:|\[|\]|\{|\}|\|/.test(candidate)
            ) {
                return text;
            }

            const participantIds = new Set();
            const participantLine = /^(?:participant|actor)\s+([A-Za-z][A-Za-z0-9_]*)\b/i;
            lines.slice(declarationIndex + 1).forEach((line) => {
                const match = line.trim().match(participantLine);
                if (match) participantIds.add(match[1]);
            });

            const messageLine = new RegExp(`^([A-Za-z][A-Za-z0-9_]*)\\s*${sequenceArrow}\\s*([A-Za-z][A-Za-z0-9_]*)\\s*:`);
            for (let index = statementIndex + 1; index < lines.length; index += 1) {
                const match = lines[index].trim().match(messageLine);
                if (!match) continue;
                lines[statementIndex] = participantIds.has(match[1])
                    ? `    %% ${candidate}`
                    : `    participant ${match[1]} as ${candidate}`;
                return lines.join('\n');
            }

            return text;
        }

        function normalizeMermaidCodeBlock(code) {
            const raw = (code || '').trim();
            if (!raw) return raw;

            let body = raw.replace(/%%\{init:[\s\S]*?\}%%/gi, ' ');
            body = normalizeMermaidArrowSyntax(body).replace(/[ \t]+/g, ' ').trim();
            body = body.replace(/\s+(?=(?:classDef|class|style|linkStyle)\b)/gi, '\n    ');
            body = body.replace(/\b((?:flowchart|graph)\s+(?:TB|TD|BT|RL|LR))\s+(?=\S)/i, '$1\n    ');
            body = normalizeSequenceMermaidStatements(body);
            body = body.replace(/\b(sequenceDiagram|stateDiagram-v2|stateDiagram|classDiagram|erDiagram|journey|gantt|pie)\s+(?=\S)/i, '$1\n    ');
            body = body.replace(/((?:[\]\)\}]|\b[A-Za-z][A-Za-z0-9_]*))\s+(?=[A-Za-z][A-Za-z0-9_]*\s*(?:-->|---|-\.->|-\.|==>|--|==))/g, '$1\n    ');
            const isClassDiagram = /^\s*classDiagram\b/im.test(body);

            const lines = [];
            body.split(/\r?\n/).forEach((line) => {
                const cleaned = normalizeMermaidEdgeLabelLine(line.trim());
                if (!cleaned) return;
                if (/^(?:classDef|style|linkStyle)\b/i.test(cleaned)) return;
                if (!isClassDiagram && /^class\b/i.test(cleaned)) return;
                const isDeclaration = /^(flowchart|graph|sequenceDiagram|stateDiagram|classDiagram|erDiagram|journey|gantt|pie)\b/i.test(cleaned);
                if (lines.length && !isDeclaration && !cleaned.startsWith('%%{')) {
                    lines.push(`    ${cleaned}`);
                } else {
                    lines.push(cleaned);
                }
            });

            return lines.join('\n').trim();
        }

        function looksLikeMermaidCode(code) {
            const text = String(code || '')
                .replace(/%%\{init:[\s\S]*?\}%%/gi, ' ')
                .trim();
            if (!text) return false;

            return /^(?:flowchart|graph)\s+(?:TB|TD|BT|RL|LR)\b/i.test(text)
                || /^(?:sequenceDiagram|stateDiagram-v2|stateDiagram|classDiagram|erDiagram|journey|gantt|pie)\b/i.test(text);
        }

        function getMermaidCodeBlocks(root) {
            if (!root) return [];
            const codeBlocks = Array.from(root.querySelectorAll('pre code'));
            const directPreBlocks = Array.from(root.querySelectorAll('pre'))
                .filter((pre) => !pre.querySelector('code'));
            return [...codeBlocks, ...directPreBlocks].filter((codeBlock) => {
                if (
                    codeBlock.classList.contains('language-mermaid')
                    || codeBlock.classList.contains('mermaid')
                ) {
                    return true;
                }
                return looksLikeMermaidCode(codeBlock.textContent || '');
            });
        }

        function cleanMermaidEdgeLabel(label) {
            return String(label || '')
                .replace(/\s+/g, ' ')
                .trim()
                .replace(/^[.:;—–\-\s]+|[.:;—–\-\s]+$/g, '')
                .replace(/\|/g, '/');
        }

        function normalizeMermaidEdgeLabelLine(line) {
            const text = String(line || '').trim();
            if (!text || text.includes('|')) return text;

            const node = '([A-Za-z][A-Za-z0-9_]*(?:\\s*(?:\\[[^\\]\\n]*\\]|\\([^\\)\\n]*\\)|\\{[^\\}\\n]*\\}))?)';
            const repairs = [
                { rx: new RegExp(`^${node}\\s*-\\.\\s*([^|<>\\n]+?)\\s*\\.->\\s*${node}$`), arrow: '-.->' },
                { rx: new RegExp(`^${node}\\s*--\\s*([^|<>\\n]+?)\\s*-->\\s*${node}$`), arrow: '-->' },
                { rx: new RegExp(`^${node}\\s*==\\s*([^|<>\\n]+?)\\s*==>\\s*${node}$`), arrow: '==>' },
            ];

            for (const repair of repairs) {
                const match = text.match(repair.rx);
                if (!match) continue;
                const label = cleanMermaidEdgeLabel(match[2]);
                if (!label) return text;
                return `${match[1].trim()} ${repair.arrow}|${label}| ${match[3].trim()}`;
            }

            return text;
        }

        function normalizeInlineMermaidFences(markdown) {
            const repaired = repairBrokenMermaidFences(markdown || '');
            const normalized = repaired.replace(/```mermaid\s+([\s\S]*?)```/gi, (_match, body) => {
                const code = normalizeMermaidCodeBlock(body);
                return `\n\`\`\`mermaid\n${code}\n\`\`\`\n`;
            });
            return normalized.replace(/([^\n])([ \t]*```mermaid)/gi, '$1\n\n```mermaid');
        }

        function repairBrokenMermaidFences(markdown) {
            let text = String(markdown || '');
            text = text.replace(/([^\n])([ \t]*```mermaid)/gi, '$1\n\n```mermaid');
            text = closeBrokenMermaidFences(text);
            text = text.replace(
                /\s*<p\b[^>]*text-align\s*:\s*center[^>]*>([\s\S]*?)<\/p>\s*(?:<\/div>\s*)*/gi,
                (_match, caption) => `\n\n*${stripHtmlTags(caption).trim()}*\n\n`
            );
            text = text.replace(/\n\s*```\s*\n(?=\s*(?:#{1,6}\s+|\*\*(?:Контекст|Пример|Вопросы|Практика|Ожидаемый|Критерии|Ситуация|Ограничение|Входные данные|Цель|Подход)\b))/gi, '\n\n');
            text = text.replace(/<\/div>\s*<\/div>/gi, '');
            return text;
        }

        function closeBrokenMermaidFences(markdown) {
            const lines = String(markdown || '').replace(/\r\n/g, '\n').split('\n');
            const output = [];
            let inFence = false;
            let fenceLanguage = '';

            const isSectionBoundary = (line) => (
                /^\s*#{1,6}\s+/.test(line)
                || /^\s*\*\*(?:Контекст|Пример|Вопросы|Практика|Ожидаемый|Критерии|Ситуация|Ограничение|Входные данные|Цель|Подход)\b/i.test(line)
                || /^\s*<p\b[^>]*text-align\s*:\s*center/i.test(line)
            );

            for (let index = 0; index < lines.length; index += 1) {
                const line = lines[index];
                const trimmed = line.trim();
                const fenceMatch = /^```([^\s`]*)/.exec(trimmed);

                if (inFence) {
                    if (trimmed === '```') {
                        output.push('```');
                        inFence = false;
                        fenceLanguage = '';
                        continue;
                    }
                    if (fenceLanguage === 'mermaid' && isSectionBoundary(line)) {
                        output.push('```');
                        inFence = false;
                        fenceLanguage = '';
                    } else {
                        output.push(line);
                        continue;
                    }
                }

                if (fenceMatch) {
                    const nextLine = lines[index + 1] || '';
                    const previousNonEmpty = [...output].reverse().find(item => item.trim()) || '';
                    const isLikelyOrphanClosing = !fenceMatch[1] && (isSectionBoundary(nextLine) || /^\*.+\*$/.test(previousNonEmpty.trim()));
                    if (isLikelyOrphanClosing) {
                        continue;
                    }
                    inFence = true;
                    fenceLanguage = String(fenceMatch[1] || '').toLowerCase();
                    output.push(fenceLanguage === 'mermaid' ? '```mermaid' : line);
                    continue;
                }

                output.push(line);
            }

            if (inFence) {
                output.push('```');
            }
            return output.join('\n');
        }

        function stripHtmlTags(value) {
            return String(value || '').replace(/<[^>]+>/g, '');
        }

        function normalizeExampleBlocks(markdown) {
            return applyOutsideFencedBlocks(markdown || '', (chunk) => {
                let text = chunk.replace(/(^|[\s([{>])Пример\s*:/gim, '$1**Пример:**');
                text = text.replace(/\n{1,2}\s*\*\*Пример:\*\*/gi, '\n\n**Пример:**');
                text = text.replace(/([^\n])\s+\*\*Пример:\*\*/gi, '$1\n\n**Пример:**');
                return text;
            });
        }

        function normalizeStrayLeadingSentenceDots(markdown) {
            return applyOutsideFencedBlocks(markdown || '', (chunk) => (
                chunk.replace(/(^|\n)([ \t]*)\.\s+(?=(?:\*\*)?[A-ZА-ЯЁ])/g, '$1$2')
            ));
        }

        function normalizeStaticInstructionMarkdown(markdown) {
            const blockNames = [
                'Контекст и ограничения проекта',
                'Как учиться в проекте',
                'Как работать с проектом',
                'Дисклеймер'
            ];
            let text = markdown || '';
            text = text.replace(
                /(Эта инструкция задаёт \*\*общие правила работы с проектом\*\* и \*\*не описывает конкретные шаги по решению задач\*\*\.)\s*/i,
                '$1\n\n'
            );
            blockNames.forEach((name) => {
                const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
                const rx = new RegExp(`\\s*(\\*\\*${escaped}\\*\\*)\\s*`, 'gi');
                text = text.replace(rx, '\n\n$1\n\n');
            });
            text = text.replace(/\s+(—\s+\*\*[^*]+:\*\*)/g, '\n\n$1');
            text = text.replace(/(\.\s+)(?=—\s+\*\*)/g, '.\n\n');
            text = text.replace(/\n{3,}/g, '\n\n');
            return text.trim();
        }

        function normalizeMarkdownForDisplay(markdown) {
            let normalized = markdown || '';
            normalized = normalizeStaticInstructionMarkdown(normalized);
            normalized = normalizeInlineMermaidFences(normalized);
            normalized = normalizeInlineMarkdownTables(normalized);
            normalized = normalizeExampleBlocks(normalized);
            normalized = normalizeStrayLeadingSentenceDots(normalized);
            return normalized;
        }

        function scheduleFormulaCheck(root) {
            setTimeout(() => {
                if (!root) return;
                const leftover = root.innerHTML.includes('FORMULABLOCK') || root.innerHTML.includes('FORMULAINLINE');
                if (leftover) {
                    console.warn('[MathJax] Обнаружены незамененные плейсхолдеры формул.');
                }
                const rawLatex = root.textContent && /\$\$[^$]+\$\$/.test(root.textContent);
                if (rawLatex) {
                    console.warn('[MathJax] Формулы отображаются как сырые $$...$$. Проверить загрузку MathJax.');
                }
            }, 50);
        }

        // Обработка ошибок JavaScript

        function markdownRenderOptionsForContainer(container, containerId) {
            const id = String(containerId || container?.id || '');
            const checkerReadmePreview = document.body?.classList?.contains('page-checker')
                && (id === 'readmePreview' || id === 'improvedReadmePreview');
            if (checkerReadmePreview) {
                return { diagramContext: 'checker' };
            }
            return {};
        }

        function displayMarkdown(markdown, containerId) {
            const container = document.getElementById(containerId);
            if (!container) {
                console.error('[displayMarkdown] Контейнер не найден:', containerId);
                return;
            }

            renderMarkdownPreview(container, markdown, markdownRenderOptionsForContainer(container, containerId));
        }

        async function renderMarkdownPreview(container, markdown, options = {}) {
            if (!container) {
                console.error('[renderMarkdownPreview] Контейнер не передан');
                return;
            }

            const renderToken = beginMarkdownRender(container);
            const renderOptions = { ...options, renderToken };

            if (!markdown || typeof markdown !== 'string') {
                container.innerHTML = `<div class="info-box">${options.emptyMessage || 'Контент отсутствует'}</div>`;
                return;
            }

            // 0. Чистим старые плейсхолдеры формул, если они были сохранены в тексте
            // (артефакты вида FORMULA_BLOCK_5 / FORMULA_INLINE_2 из предыдущих версий пайплайна)
            markdown = markdown.replace(/FORMULA_(?:BLOCK|INLINE)_\d+/g, '').trim();
            markdown = normalizeMarkdownForDisplay(markdown);

            // 1. Защищаем формулы от обработки marked.js
            const formulaGuards = protectFormulas(markdown);
            let protectedMarkdown = formulaGuards.markdown;

            // 2. Парсим markdown в HTML. Marked грузим лениво, чтобы первый экран не ждал Markdown-preview.
            try {
                await ensureMarkedLoaded();
            } catch (err) {
                console.error('[displayMarkdown] marked.js не загрузился:', err);
            }
            if (!isMarkdownRenderCurrent(container, renderToken)) return;
            if (typeof window.marked === 'undefined') {
                console.error('[displayMarkdown] marked.js не загружен');
                if (window.sanitize) {
                    window.sanitize.safeSetErrorMessage(container, 'Ошибка: библиотека marked.js не загружена');
                } else {
                    container.textContent = 'Ошибка: библиотека marked.js не загружена';
                }
                return;
            }

            let html = window.marked.parse(protectedMarkdown);

            html = restoreFormulas(html, formulaGuards);
            if (!isMarkdownRenderCurrent(container, renderToken)) return;

            // 3. Вставляем HTML в контейнер (с санитизацией)
            if (window.sanitize) {
                window.sanitize.safeSetMarkdownHTML(container, html);
            } else {
                container.innerHTML = html;
            }
            normalizeRenderedTaskLists(container);

            // 3.1. Чистим артефакты Markdown-таблиц вида "| Точность (Precision) |"
            // которые приходят как обычные параграфы после парсинга.
            const paras = container.querySelectorAll('p');
            paras.forEach(p => {
                const text = p.textContent || '';
                const m = text.match(/^\s*\|\s*([^|]+?)\s*\|\s*$/);
                if (m) {
                    p.textContent = m[1].trim();
                }
            });

            // 4. Оборачиваем таблицы в "wrapper" с горизонтальной прокруткой
            wrapMarkdownTables(container);

            convertLatexLikeTableCells(container);

            // 5. Рендерим Mermaid-диаграммы
            await renderMermaidDiagrams(container, renderOptions);
            if (!isMarkdownRenderCurrent(container, renderToken)) return;
            wrapDiagramImages(container);

            // 6. Рендерим MathJax-формулы
            await typesetMathJax(container, renderToken);
            if (!isMarkdownRenderCurrent(container, renderToken)) return;

            // 7. Подменяем локальные изображения для диаграмм, если они закодированы в ответе
            hydrateLocalImages(container, renderOptions);
            scheduleFormulaCheck(container);
        }

        function wrapMarkdownTables(root) {
            if (!root) return;
            const tables = root.querySelectorAll('table');
            tables.forEach(table => {
                let wrapper = table.parentElement?.classList.contains('table-wrapper')
                    ? table.parentElement
                    : null;
                if (!wrapper) {
                    if (!table.parentNode) return;
                    wrapper = document.createElement('div');
                    wrapper.className = 'table-wrapper';
                    table.parentNode.insertBefore(wrapper, table);
                    wrapper.appendChild(table);
                }
                if (wrapper && !wrapper.nextElementSibling?.classList.contains('table-caption')) {
                    const captionText = extractTableCaption(wrapper.nextElementSibling);
                    if (captionText) {
                        const caption = document.createElement('div');
                        caption.className = 'table-caption';
                        caption.textContent = simplifyTableCaption(captionText);
                        wrapper.insertAdjacentElement('afterend', caption);
                    }
                }
            });
        }

        function normalizeRenderedTaskLists(root) {
            if (!root) return;
            root.querySelectorAll('li').forEach(item => {
                const firstMeaningfulChild = [...item.childNodes].find(child => (
                    child.nodeType !== Node.TEXT_NODE || String(child.textContent || '').trim()
                ));
                if (firstMeaningfulChild?.matches?.('input[type="checkbox"]')) {
                    item.classList.add('task-list-item');
                    item.parentElement?.classList.add('contains-task-list');
                    firstMeaningfulChild.disabled = true;
                    wrapTaskListItemContent(item, firstMeaningfulChild);
                }
            });
        }

        function wrapTaskListItemContent(item, checkbox) {
            if (!item || !checkbox) return;
            if ([...item.children].some(child => child.classList?.contains('task-list-content'))) {
                return;
            }

            const content = document.createElement('span');
            content.className = 'task-list-content';
            const trailingNodes = [];
            let checkboxSeen = false;

            [...item.childNodes].forEach(node => {
                if (node === checkbox) {
                    checkboxSeen = true;
                    return;
                }
                if (!checkboxSeen && node.nodeType === Node.TEXT_NODE && !String(node.textContent || '').trim()) {
                    node.remove();
                    return;
                }
                if (checkboxSeen) {
                    trailingNodes.push(node);
                }
            });

            checkbox.insertAdjacentElement('afterend', content);
            trailingNodes.forEach(node => content.appendChild(node));
        }

        function convertLatexLikeTableCells(root) {
            if (!root) return;
            const cells = root.querySelectorAll('td, th');
            cells.forEach(cell => {
                if (!cell) return;
                const raw = cell.textContent ? cell.textContent.trim() : '';
                if (!raw || raw.length > 400) return;
                if (raw.includes('$') || raw.includes('$$')) return;
                const hasLatex =
                    /\\[a-zA-Z]+/.test(raw) ||
                    /[A-Za-z0-9]+\_[A-Za-z0-9]/.test(raw) ||
                    raw.includes('^') ||
                    raw.includes('{') ||
                    raw.includes('}');
                if (!hasLatex) return;
                // Сохраняем пробелы компактно
                const latex = raw.replace(/\s+/g, ' ').trim();
                cell.textContent = `$${latex}$`;
            });
        }

        /**
         * Ищет mermaid-блоки и рендерит их через mermaid.render.
         * Ожидается, что markdown размечен либо как ```mermaid, либо как ```mermaid\n...\n```.
         * Marked в таком случае сделает <pre><code class="language-mermaid">...</code></pre>.
         */
        function diagramRenderContext(root, options = {}) {
            if (options.diagramContext) {
                return String(options.diagramContext);
            }
            if (
                root?.classList?.contains('methodology-markdown-preview')
                || root?.closest?.('.methodology-markdown-preview')
            ) {
                return 'methodology';
            }
            return 'default';
        }

        async function renderMermaidDiagrams(root, options = {}) {
            // Ищем размеченные mermaid-блоки и fallback-блоки, где модель забыла язык fence.
            const codeBlocks = getMermaidCodeBlocks(root);
            if (!codeBlocks.length) return;

            try {
                await ensureMermaidLoaded();
            } catch (err) {
                console.warn('[Mermaid] mermaid.js не загрузился — диаграммы будут показаны как код', err);
                return;
            }

            if (typeof window.mermaid === 'undefined') {
                // Просто оставляем код как есть, без падения
                console.warn('[Mermaid] mermaid.js не загружен — диаграммы будут показаны как код');
                return;
            }

            const renderContext = diagramRenderContext(root, options);
            const renderToken = options.renderToken || '';

            const renderTasks = codeBlocks.map((codeBlock, index) => {
                const pre = codeBlock.closest('pre');
                const code = normalizeMermaidCodeBlock(codeBlock.textContent.trim());
                if (!pre || !code || !pre.parentNode || !isMarkdownRenderCurrent(root, renderToken)) {
                    return Promise.resolve();
                }

                const figure = document.createElement('figure');
                figure.className = 'diagram-figure';

                const holder = document.createElement('div');
                holder.className = 'mermaid-diagram';
                holder.dataset.diagramContext = renderContext;

                try {
                    pre.parentNode.replaceChild(figure, pre);
                } catch (err) {
                    ignoreStaleRenderError(err, root, renderToken, 'Mermaid');
                    return Promise.resolve();
                }
                figure.appendChild(holder);

                const captionText = extractDiagramCaption(figure.nextElementSibling);
                if (captionText) {
                    const caption = document.createElement('figcaption');
                    caption.className = 'diagram-caption';
                    caption.textContent = simplifyDiagramCaption(captionText);
                    figure.appendChild(caption);
                }

                const renderId = 'mermaid-' + Date.now() + '-' + index;

                // Без повторной initialize — предполагаем, что она уже была вызвана один раз где-то сверху
                return Promise.resolve()
                    .then(() => (typeof window.mermaid.parse === 'function' ? window.mermaid.parse(code) : true))
                    .then(() => window.mermaid.render(renderId, code))
                    .then(res => {
                        if (!isMarkdownRenderCurrent(root, renderToken) || !holder.isConnected) return;
                        // В новых версиях mermaid res уже объект { svg, bindFunctions }, в старых — просто svg-строка
                        const svg = typeof res === 'string' ? res : res.svg;
                        holder.innerHTML = svg;
                        
                        // Делаем svg адаптивным
                        const svgEl = holder.querySelector('svg');
                        if (svgEl) {
                            normalizeMermaidSvg(svgEl, holder, code, renderContext);
                            centerMermaidLabels(svgEl, holder);
                            svgEl.removeAttribute('height');
                            svgEl.removeAttribute('width');
                            svgEl.setAttribute('preserveAspectRatio', 'xMidYMid meet');
                            svgEl.style.width = 'var(--diagram-width, auto)';
                            svgEl.style.maxWidth = holder.dataset.diagramOverflow === 'scroll' ? 'none' : '100%';
                            svgEl.style.height = 'auto';
                            svgEl.style.margin = '0 auto';
                            svgEl.style.display = 'block';
                            holder.classList.add('mermaid-ready');
                            enableDiagramZoom(holder, svgEl, captionText || 'Диаграмма');
                            centerScrollableMermaid(holder);
                            stabilizeRenderedMermaid(holder, svgEl);
                        }
                    })
                    .catch(err => {
                        if (ignoreStaleRenderError(err, root, renderToken, 'Mermaid')) return;
                        console.error('[Mermaid] Ошибка рендеринга диаграммы:', err);
                        if (!holder.isConnected) return;
                        holder.innerHTML =
                            '<div class="error-msg">Ошибка отображения диаграммы: ' +
                            (err.message || 'Неизвестная ошибка') +
                            '</div>';
                    });
            });
            await Promise.all(renderTasks);
        }

        function wrapDiagramImages(root) {
            if (!root) return;
            const images = root.querySelectorAll('img');
            images.forEach(img => {
                if (img.closest('figure.diagram-figure')) return;
                const src = img.getAttribute('src') || '';
                const alt = img.getAttribute('alt') || '';
                const isDiagramAlt = /^(?:диаграмма|схема|процесс|алгоритм)(?:\s|[:.—-]|$)/i.test(alt);
                const isGeneratedDiagram = /^images\/diagram_\d+\.png(?:[?#].*)?$/i.test(src)
                    || /^data:image\/png;base64,/i.test(src) && isDiagramAlt
                    || isDiagramAlt;
                if (!isGeneratedDiagram || !img.parentNode) return;

                const figure = document.createElement('figure');
                figure.className = 'diagram-figure';
                const surface = document.createElement('div');
                surface.className = 'diagram-image-surface';
                const host = img.parentElement;
                const imageOnlyParagraph = host?.tagName === 'P'
                    && [...host.childNodes].every(node => node === img || !String(node.textContent || '').trim());
                if (imageOnlyParagraph && host.parentNode) {
                    host.parentNode.insertBefore(figure, host);
                } else {
                    img.parentNode.insertBefore(figure, img);
                }
                surface.appendChild(img);
                figure.appendChild(surface);
                if (imageOnlyParagraph) {
                    host.remove();
                }

                const captionText = extractDiagramCaption(figure.nextElementSibling) || alt;
                if (captionText) {
                    const caption = document.createElement('figcaption');
                    caption.className = 'diagram-caption';
                    caption.textContent = simplifyDiagramCaption(captionText);
                    figure.appendChild(caption);
                }
                enableDiagramZoom(surface, img, captionText || alt || 'Диаграмма');
            });
        }

        function simplifyDiagramCaption(text) {
            const cleaned = String(text || '')
                .replace(/\s+/g, ' ')
                .replace(/^(рис\.?\s*\d*|схема|диаграмма|процесс|алгоритм|таблица)\s*[:.—-]?\s*/i, '')
                .trim();
            const firstSentence = cleaned.split(/(?<=[.!?])\s+/)[0] || cleaned;
            const caption = firstSentence.length <= 72
                ? firstSentence
                : `${firstSentence.slice(0, 69).replace(/\s+\S*$/, '').trim()}...`;
            return sentenceCaseCaption(caption);
        }

        function extractDiagramCaption(nextElement) {
            return extractDisplayCaption(nextElement, {
                prefixRegex: /^(?:рис\.?\s*\d*|схема|диаграмма|процесс|алгоритм|таблица)(?:\s|[:.—-]|$)/i,
                allowLeadingEmphasis: true
            });
        }

        function extractTableCaption(nextElement) {
            return extractDisplayCaption(nextElement, {
                prefixRegex: /^(?:табл\.?|таблица)(?:\s|\d|[:.—-]|$)/i,
                allowLeadingEmphasis: true
            });
        }

        function simplifyTableCaption(text) {
            const cleaned = String(text || '')
                .replace(/\s+/g, ' ')
                .replace(/^(таблица\s*\d*|табл\.?\s*\d*)\s*[:.—-]?\s*/i, '')
                .trim();
            const caption = cleaned.length <= 96
                ? cleaned
                : `${cleaned.slice(0, 93).replace(/\s+\S*$/, '').trim()}...`;
            return sentenceCaseCaption(caption);
        }

        function sentenceCaseCaption(text) {
            const value = String(text || '').trim();
            const firstLetterIndex = value.search(/[A-Za-zА-Яа-яЁё]/);
            if (firstLetterIndex < 0) return value;
            return `${value.slice(0, firstLetterIndex)}${value.charAt(firstLetterIndex).toUpperCase()}${value.slice(firstLetterIndex + 1)}`;
        }

        function extractDisplayCaption(nextElement, options = {}) {
            if (!nextElement || nextElement.tagName !== 'P') return '';

            const prefixRegex = options.prefixRegex || /^$/;
            const allowLeadingEmphasis = !!options.allowLeadingEmphasis;
            const nextText = (nextElement.textContent || '').trim();
            const firstMeaningfulChild = [...nextElement.childNodes].find(node => (
                node.nodeType !== Node.TEXT_NODE || String(node.textContent || '').trim()
            ));
            const firstElement = firstMeaningfulChild?.nodeType === Node.ELEMENT_NODE
                ? firstMeaningfulChild
                : null;
            const hasOnlyEm = nextElement.children.length === 1 && nextElement.firstElementChild?.tagName === 'EM';
            const looksLikeCaption = prefixRegex.test(nextText) || hasOnlyEm;

            if (looksLikeCaption) {
                nextElement.remove();
                return nextText;
            }

            if (allowLeadingEmphasis && firstElement?.tagName === 'EM') {
                const captionText = (firstElement.textContent || '').trim();
                firstElement.remove();
                trimLeadingText(nextElement);
                if (!(nextElement.textContent || '').trim()) {
                    nextElement.remove();
                }
                return captionText;
            }

            return '';
        }

        function trimLeadingText(element) {
            while (element.firstChild) {
                const node = element.firstChild;
                if (node.nodeType !== Node.TEXT_NODE) break;
                const cleaned = String(node.textContent || '').replace(/^[\s:—-]+/, '');
                if (cleaned) {
                    node.textContent = cleaned;
                    break;
                }
                node.remove();
            }
        }

        function normalizeMermaidSvg(svgEl, holder, code = '', renderContext = 'default') {
            if (!svgEl || !holder) return;
            const viewBox = svgEl.getAttribute('viewBox');
            const rawWidth = parseFloat(svgEl.getAttribute('width') || '');
            const rawHeight = parseFloat(svgEl.getAttribute('height') || '');

            if (!viewBox && rawWidth && rawHeight) {
                svgEl.setAttribute('viewBox', `0 0 ${rawWidth} ${rawHeight}`);
            }

            const vb = svgEl.viewBox && svgEl.viewBox.baseVal;
            const width = vb && vb.width ? vb.width : rawWidth;
            const height = vb && vb.height ? vb.height : rawHeight;
            const metrics = mermaidCodeMetrics(code);
            const isMethodology = renderContext === 'methodology';
            const isChecker = renderContext === 'checker';
            const profile = isMethodology
                ? {
                    baseWidth: 700,
                    compactWidth: 560,
                    tallWidth: 680,
                    complexWidth: 760,
                    wideMinWidth: 760,
                    wideMaxWidth: 980,
                    boxWidth: 900,
                    maxNaturalWidth: 1180,
                    maxEstimatedHeight: 560,
                    wideFontSize: '13.5px',
                    normalFontSize: '14px',
                }
                : isChecker
                ? {
                    baseWidth: 680,
                    compactWidth: 540,
                    tallWidth: 660,
                    complexWidth: 740,
                    wideMinWidth: 740,
                    wideMaxWidth: 940,
                    boxWidth: 860,
                    maxNaturalWidth: 1120,
                    maxEstimatedHeight: 520,
                    wideFontSize: '13.5px',
                    normalFontSize: '14px',
                }
                : {
                    baseWidth: 720,
                    compactWidth: 560,
                    tallWidth: 700,
                    complexWidth: 800,
                    wideMinWidth: 800,
                    wideMaxWidth: 1020,
                    boxWidth: 920,
                    maxNaturalWidth: 1240,
                    maxEstimatedHeight: 580,
                    wideFontSize: '13.5px',
                    normalFontSize: '14px',
                };
            const naturalWidth = Math.max(320, Math.min(width || 720, profile.maxNaturalWidth));
            const naturalHeight = Math.max(180, Math.min(height || 520, 2200));
            const aspectRatio = naturalHeight / Math.max(naturalWidth, 1);
            const complexity = metrics.statementCount + Math.ceil(metrics.maxLineLength / 48);
            const isWide = naturalWidth > 980 || metrics.maxLineLength > 84;
            const isTall = aspectRatio > 1.18 || naturalHeight > 900;
            let renderWidth = profile.baseWidth;
            if (isWide) {
                renderWidth = Math.min(Math.max(naturalWidth, profile.wideMinWidth), profile.wideMaxWidth);
            } else if (complexity <= 4 && naturalWidth < 620) {
                renderWidth = profile.compactWidth;
            } else if (isTall) {
                renderWidth = profile.tallWidth;
            } else if (complexity >= 10) {
                renderWidth = profile.complexWidth;
            }
            const estimatedHeight = Math.round((naturalHeight / Math.max(naturalWidth, 1)) * renderWidth);
            const minHeight = Math.max(190, Math.min(estimatedHeight, profile.maxEstimatedHeight));
            const boxWidth = profile.boxWidth;

            holder.style.setProperty('--diagram-width', `${Math.round(renderWidth)}px`);
            holder.style.setProperty('--diagram-box-width', `${Math.round(boxWidth)}px`);
            holder.style.setProperty('--diagram-min-height', `${minHeight}px`);
            holder.style.setProperty('--diagram-font-size', isWide ? profile.wideFontSize : profile.normalFontSize);
            holder.dataset.diagramOverflow = isWide ? 'scroll' : 'fit';

            if (isWide) {
                holder.dataset.diagramSize = 'wide';
            } else if (isTall) {
                holder.dataset.diagramSize = 'tall';
            } else if (naturalWidth < 560) {
                holder.dataset.diagramSize = 'compact';
            } else {
                holder.dataset.diagramSize = 'normal';
            }
        }

        function mermaidCodeMetrics(code) {
            const lines = String(code || '')
                .split(/\r?\n/)
                .map(line => line.trim())
                .filter(line => line && !line.startsWith('%%'));
            return {
                statementCount: lines.length,
                maxLineLength: lines.reduce((max, line) => Math.max(max, line.length), 0),
            };
        }

        function centerScrollableMermaid(holder) {
            if (!holder) return;
            window.requestAnimationFrame(() => {
                if (holder.scrollWidth > holder.clientWidth) {
                    holder.scrollLeft = Math.max(0, (holder.scrollWidth - holder.clientWidth) / 2);
                }
            });
        }

        function stabilizeRenderedMermaid(holder, svgEl) {
            if (!holder || !svgEl) return;
            window.requestAnimationFrame(() => {
                if (!holder.isConnected || !svgEl.isConnected) return;
                centerMermaidLabels(svgEl, holder);
                const rect = svgEl.getBoundingClientRect();
                if ((rect.width < 4 || rect.height < 4) && holder.dataset.diagramContext === 'methodology') {
                    svgEl.style.width = 'var(--diagram-width, 720px)';
                    svgEl.style.minWidth = 'var(--diagram-width, 720px)';
                    svgEl.style.maxWidth = 'none';
                }
                centerScrollableMermaid(holder);
            });
        }

        function ensureDiagramLightbox() {
            const existing = document.getElementById('diagramLightbox');
            if (existing) return existing;

            const lightbox = document.createElement('div');
            lightbox.id = 'diagramLightbox';
            lightbox.className = 'diagram-lightbox';
            lightbox.hidden = true;
            lightbox.innerHTML = [
                '<div class="diagram-lightbox-surface" role="dialog" aria-modal="true" aria-label="Увеличенная диаграмма">',
                '  <button type="button" class="diagram-lightbox-close" aria-label="Закрыть увеличенную диаграмму">×</button>',
                '  <div class="diagram-lightbox-content"></div>',
                '  <div class="diagram-lightbox-caption"></div>',
                '</div>',
            ].join('');
            document.body.appendChild(lightbox);

            const close = () => closeDiagramLightbox(lightbox);
            lightbox.addEventListener('click', (event) => {
                if (event.target === lightbox) close();
            });
            lightbox.querySelector('.diagram-lightbox-close')?.addEventListener('click', close);
            document.addEventListener('keydown', (event) => {
                if (!lightbox.hidden && event.key === 'Escape') {
                    close();
                }
            });
            return lightbox;
        }

        function closeDiagramLightbox(lightbox = null) {
            const target = lightbox || document.getElementById('diagramLightbox');
            if (!target) return;
            target.hidden = true;
            document.body.classList.remove('diagram-lightbox-open');
            const content = target.querySelector('.diagram-lightbox-content');
            if (content) {
                content.innerHTML = '';
            }
        }

        function diagramLightboxWidth(sourceNode) {
            if (!sourceNode || typeof sourceNode.getBoundingClientRect !== 'function') {
                return 760;
            }
            const rect = sourceNode.getBoundingClientRect();
            const sourceWidth = Math.max(rect.width || 0, sourceNode.clientWidth || 0);
            // Lightbox должен помогать рассмотреть детали, а не превращать схему в огромный холст.
            return Math.round(Math.max(420, Math.min(sourceWidth * 1.12, 1040)));
        }

        function openDiagramLightbox(sourceNode, caption = '') {
            if (!sourceNode) return;
            const lightbox = ensureDiagramLightbox();
            const content = lightbox.querySelector('.diagram-lightbox-content');
            const captionNode = lightbox.querySelector('.diagram-lightbox-caption');
            if (!content) return;

            const mediaWidth = diagramLightboxWidth(sourceNode);
            const clone = sourceNode.cloneNode(true);
            clone.removeAttribute('id');
            clone.classList.add('diagram-lightbox-media');
            clone.removeAttribute('width');
            clone.removeAttribute('height');
            clone.style.width = 'var(--diagram-lightbox-media-width)';
            clone.style.maxWidth = '';
            clone.style.height = '';
            content.style.setProperty('--diagram-lightbox-media-width', `${mediaWidth}px`);
            content.innerHTML = '';
            content.appendChild(clone);
            if (captionNode) {
                captionNode.textContent = caption || '';
                captionNode.hidden = !caption;
            }
            document.body.classList.add('diagram-lightbox-open');
            lightbox.hidden = false;
            lightbox.querySelector('.diagram-lightbox-close')?.focus({ preventScroll: true });
        }

        function enableDiagramZoom(surface, sourceNode, caption = '') {
            if (!surface || !sourceNode || surface.dataset.diagramZoomReady === 'true') return;
            surface.dataset.diagramZoomReady = 'true';
            surface.classList.add('diagram-zoomable');
            surface.tabIndex = 0;
            surface.setAttribute('role', 'button');
            surface.setAttribute('aria-label', 'Увеличить диаграмму');

            const control = document.createElement('button');
            control.type = 'button';
            control.className = 'diagram-zoom-control';
            control.setAttribute('aria-label', 'Увеличить диаграмму');
            control.textContent = '↗';
            surface.appendChild(control);

            const open = (event) => {
                event.preventDefault();
                event.stopPropagation();
                openDiagramLightbox(sourceNode, caption);
            };
            control.addEventListener('click', open);
            surface.addEventListener('click', (event) => {
                if (event.target === control) return;
                open(event);
            });
            surface.addEventListener('keydown', (event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                    open(event);
                }
            });
        }

        function centerMermaidLabels(svgEl, holder = null) {
            if (!svgEl) return;

            const fontSize = holder?.style?.getPropertyValue('--diagram-font-size') || '16px';
            const labelNodes = svgEl.querySelectorAll(
                '.nodeLabel, .edgeLabel, .label, foreignObject div, foreignObject span'
            );
            labelNodes.forEach(label => {
                const isEdgeLabel = Boolean(label.closest?.('.edgeLabel'));
                label.style.textAlign = 'center';
                label.style.lineHeight = '1.22';
                label.style.whiteSpace = isEdgeLabel ? 'nowrap' : 'normal';
                label.style.wordBreak = isEdgeLabel ? 'keep-all' : 'normal';
                label.style.overflowWrap = 'normal';
                label.style.hyphens = 'none';
                label.style.fontSize = fontSize;
                label.style.fontWeight = '600';
                if (label.closest('foreignObject')) {
                    label.style.display = 'flex';
                    label.style.alignItems = 'center';
                    label.style.justifyContent = 'center';
                    label.style.width = isEdgeLabel ? 'auto' : '100%';
                    label.style.minWidth = isEdgeLabel ? 'max-content' : '';
                    label.style.height = '100%';
                    label.style.boxSizing = 'border-box';
                    label.style.padding = '0 6px';
                }
                label.querySelectorAll?.('p').forEach(paragraph => {
                    paragraph.style.margin = '0';
                    paragraph.style.textAlign = 'center';
                    paragraph.style.lineHeight = '1.22';
                    paragraph.style.wordBreak = isEdgeLabel ? 'keep-all' : 'normal';
                    paragraph.style.overflowWrap = 'normal';
                    paragraph.style.hyphens = 'none';
                    if (isEdgeLabel) {
                        paragraph.style.whiteSpace = 'nowrap';
                        paragraph.style.minWidth = 'max-content';
                    }
                });
            });
        }

        function hydrateLocalImages(root, options = {}) {
            const result = options.currentResult || window.currentResult || window.__contentGenCurrentResult || null;
            if (!root || !result || !result.assets) {
                return;
            }
            const assets = result.assets;
            const map = new Map();

            const images = Array.isArray(assets.images) ? assets.images : [];
            images.forEach(img => {
                if (img && img.name && img.data) {
                    map.set(img.name, img.data);
                }
            });

            const files = Array.isArray(assets.files) ? assets.files : [];
            files.forEach(file => {
                const path = file && (file.path || file.name);
                if (!path || !file.data) {
                    return;
                }
                const name = path.split('/').pop();
                if (name && !map.has(name)) {
                    map.set(name, file.data);
                }
            });

            if (!map.size) {
                return;
            }

            const imgNodes = root.querySelectorAll('img');
            imgNodes.forEach(img => {
                const src = img.getAttribute('src');
                if (!src || !src.startsWith('images/')) {
                    return;
                }
                const name = src.split('/').pop();
                const base64 = map.get(name);
                if (base64) {
                    img.src = `data:image/png;base64,${base64}`;
                }
            });
        }

        function normalizeMathBlocks(root) {
            if (!root) return;
            const blocks = root.querySelectorAll('mjx-container[display="block"], .MathJax_Display, .MathJax_SVG_Display');
            if (!blocks.length) return;
            blocks.forEach(block => {
                if (!block) {
                    return;
                }
                const parent = block.parentElement;
                const wrapperClass = 'math-center-wrapper';

                const isAlreadyWrapped = parent && parent.classList.contains(wrapperClass);
                if (!isAlreadyWrapped) {
                    const wrapper = document.createElement('div');
                    wrapper.classList.add(wrapperClass);

                    const canReplaceParent = parent &&
                        parent.parentNode &&
                        Array.from(parent.childNodes).every(node => {
                            if (node === block) return true;
                            if (node.nodeType === Node.TEXT_NODE) {
                                return !node.textContent.trim();
                            }
                            return false;
                        });

                    if (canReplaceParent) {
                        parent.parentNode.insertBefore(wrapper, parent);
                        wrapper.appendChild(block);
                        parent.remove();
                    } else if (parent) {
                        parent.insertBefore(wrapper, block);
                        wrapper.appendChild(block);
                    } else {
                        root.appendChild(wrapper);
                        wrapper.appendChild(block);
                    }
                }

                const host = block.parentElement;
                if (host) {
                    host.classList.add(wrapperClass);
                }

                block.classList.add('math-display-block');
                block.style.setProperty('text-align', 'center', 'important');
                block.style.setProperty('justify-content', 'center', 'important');
                block.style.setProperty('display', 'flex', 'important');
                block.style.setProperty('margin', '0 auto', 'important');
                block.style.setProperty('width', '100%', 'important');
                const svg = block.querySelector('svg');
                if (svg) {
                    svg.style.setProperty('margin', '0 auto', 'important');
                    svg.style.setProperty('max-width', '100%', 'important');
                    svg.style.setProperty('height', 'auto', 'important');
                }
            });
        }

        /**
         * Вызывает MathJax для заданного контейнера.
         * Ориентируемся на MathJax v3 (typesetPromise), с fallback на typeset.
         * Дожидается загрузки MathJax, если он еще не готов.
         */
        function typesetMathJax(root, renderToken = '') {
            if (!root) return Promise.resolve();
            if (!isMarkdownRenderCurrent(root, renderToken)) return Promise.resolve();
            if (!rootHasMath(root) && !(window.MathJax && window.MathJax.startup)) {
                normalizeMathBlocks(root);
                return Promise.resolve();
            }

            const waitForMathJax = resolve => {
                if (!isMarkdownRenderCurrent(root, renderToken)) {
                    resolve();
                    return;
                }
                if (window.MathJax && window.MathJax.startup && window.MathJax.startup.promise) {
                    window.MathJax.startup.promise.then(() => resolve()).catch(resolve);
                    return;
                }
                if (window.MathJax) {
                    resolve();
                    return;
                }
                setTimeout(() => waitForMathJax(resolve), 100);
            };

            return ensureMathJaxLoaded()
                .catch(err => {
                    console.error('[MathJax] Библиотека не загрузилась:', err);
                })
                .then(() => new Promise(waitForMathJax))
                .then(() => {
                    if (!isMarkdownRenderCurrent(root, renderToken)) return;
                    const mj = window.MathJax;
                    if (!mj) return;
                    if (typeof mj.typesetPromise === 'function') {
                        return mj.typesetPromise([root]);
                    }
                    if (typeof mj.typeset === 'function') {
                        mj.typeset([root]);
                    }
                })
                .catch(err => {
                    if (ignoreStaleRenderError(err, root, renderToken, 'MathJax')) return;
                    console.error('[MathJax] Ошибка обработки формул:', err);
                })
                .finally(() => {
                    if (isMarkdownRenderCurrent(root, renderToken)) {
                        normalizeMathBlocks(root);
                    }
                });
        }

        if (typeof window !== 'undefined') {
            window.ContentGenMarkdownRendering = {
                displayMarkdown,
                renderMarkdownPreview,
                isMarkdownRenderCurrent,
                ignoreStaleRenderError,
                normalizeMarkdownForDisplay,
                renderMermaidDiagrams,
                wrapMarkdownTables,
                normalizeRenderedTaskLists,
                convertLatexLikeTableCells,
                normalizeMermaidCodeBlock,
                normalizeMermaidEdgeLabelLine,
                normalizeInlineMermaidFences,
                normalizeStrayLeadingSentenceDots,
                repairBrokenMermaidFences,
                closeBrokenMermaidFences,
                openDiagramLightbox,
                enableDiagramZoom,
                scheduleFormulaCheck,
                hydrateLocalImages,
                normalizeMathBlocks,
                typesetMathJax,
            };

            Object.assign(window, {
                byId,
                getChecked,
                setChecked,
                setValue,
                normalizeAudienceLevel,
                setAudienceLevel,
                splitCommaList,
                setDisplay,
                displayMarkdown,
                renderMarkdownPreview,
                normalizeMarkdownForDisplay,
                renderMermaidDiagrams,
            });
        }

