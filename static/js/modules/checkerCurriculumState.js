// Curriculum upload and selected project context for README checker improvement.

let checkerCurriculum = null;
let checkerSelectedBlock = null;
let checkerSelectedProject = null;
let checkerCurriculumContext = null;

function checkerApiUrl() {
    return window.API_URL || (window.API_BASE ? `${window.API_BASE}/api/v1` : '/api/v1');
}

async function handleCheckerCurriculumUpload(event) {
    const file = event.target.files[0];
    if (!file) return;

    const fileNameEl = document.getElementById('checkerCurriculumFileName');
    if (fileNameEl) fileNameEl.textContent = 'Загрузка...';

    const formData = new FormData();
    formData.append('file', file);

    try {
        const token = localStorage.getItem('auth_token');
        const response = await fetch(`${checkerApiUrl()}/curriculum/upload`, {
            method: 'POST',
            headers: token ? { 'Authorization': 'Bearer ' + token } : {},
            body: formData
        });
        if (!response.ok) throw new Error('Ошибка загрузки УП');

        checkerCurriculum = await response.json();
        if (fileNameEl) {
            fileNameEl.textContent = `${file.name} (${(checkerCurriculum.blocks || []).length} блоков)`;
        }
        populateCheckerCurriculumBlocks();

        const blockGroup = document.getElementById('checkerCurriculumBlockGroup');
        if (blockGroup) blockGroup.style.display = 'block';

        checkerSelectedBlock = null;
        checkerSelectedProject = null;
        checkerCurriculumContext = null;
    } catch (error) {
        if (fileNameEl) fileNameEl.textContent = '';
        alert('Не удалось загрузить УП: ' + error.message);
    }
}

function populateCheckerCurriculumBlocks() {
    const select = document.getElementById('checkerCurriculumBlock');
    if (!select) return;

    select.innerHTML = '<option value="">— Выберите блок —</option>';
    (checkerCurriculum?.blocks || []).forEach(block => {
        const option = document.createElement('option');
        option.value = block.name;
        option.textContent = block.name;
        select.appendChild(option);
    });

    const projectGroup = document.getElementById('checkerCurriculumProjectGroup');
    const projectSelect = document.getElementById('checkerCurriculumProject');
    if (projectGroup) projectGroup.style.display = 'none';
    if (projectSelect) projectSelect.innerHTML = '<option value="">— Выберите проект —</option>';
}

function onCheckerCurriculumBlockChange() {
    const blockName = document.getElementById('checkerCurriculumBlock')?.value || '';
    const projectSelect = document.getElementById('checkerCurriculumProject');
    if (!projectSelect) return;

    projectSelect.innerHTML = '<option value="">— Выберите проект —</option>';
    const projectGroup = document.getElementById('checkerCurriculumProjectGroup');
    if (projectGroup) projectGroup.style.display = blockName ? 'block' : 'none';

    checkerSelectedBlock = null;
    checkerSelectedProject = null;
    checkerCurriculumContext = null;

    if (!blockName || !checkerCurriculum?.blocks) return;

    const block = checkerCurriculum.blocks.find(candidate => candidate.name === blockName);
    if (!block?.projects) return;

    block.projects.forEach(project => {
        const option = document.createElement('option');
        option.value = project.order;
        option.textContent = `${project.order}. ${project.title || ''}`;
        option.dataset.project = JSON.stringify(project);
        option.dataset.block = JSON.stringify(block);
        projectSelect.appendChild(option);
    });
}

function onCheckerCurriculumProjectChange() {
    const projectSelect = document.getElementById('checkerCurriculumProject');
    const option = projectSelect?.options[projectSelect.selectedIndex];
    if (!option?.value) {
        checkerSelectedBlock = null;
        checkerSelectedProject = null;
        checkerCurriculumContext = null;
        return;
    }

    try {
        checkerSelectedBlock = JSON.parse(option.dataset.block || '{}');
        checkerSelectedProject = JSON.parse(option.dataset.project || '{}');
        checkerCurriculumContext = buildCheckerCurriculumContext(checkerSelectedBlock, checkerSelectedProject);
    } catch (error) {
        console.warn('Не удалось прочитать выбранный проект УП:', error);
        checkerSelectedBlock = null;
        checkerSelectedProject = null;
        checkerCurriculumContext = null;
    }
}

function buildCheckerCurriculumContext(block, currentProject) {
    if (!checkerCurriculum || !block || !currentProject) return null;

    const blockIndex = (checkerCurriculum.blocks || []).findIndex(candidate => candidate.name === block.name);
    const projectIndex = (block.projects || []).findIndex(project => project.order === currentProject.order);
    const previousProjects = (block.projects || [])
        .slice(0, projectIndex)
        .map(project => toCurriculumProjectSummary(project, block.name));
    const nextProjects = (block.projects || [])
        .slice(projectIndex + 1)
        .map(project => toCurriculumProjectSummary(project, block.name));
    const allBlockLearningOutcomes = [];

    (block.projects || []).forEach(project => {
        if (project.learning_outcomes) {
            allBlockLearningOutcomes.push(...project.learning_outcomes);
        }
    });

    const previousBlock = blockIndex > 0 ? checkerCurriculum.blocks[blockIndex - 1] : null;
    const nextBlock = blockIndex >= 0 && blockIndex < (checkerCurriculum.blocks || []).length - 1
        ? checkerCurriculum.blocks[blockIndex + 1]
        : null;

    return {
        block_name: block.name,
        block_goals: block.goals || [],
        current_project_order: currentProject.order,
        previous_projects: previousProjects,
        next_projects: nextProjects,
        all_block_learning_outcomes: allBlockLearningOutcomes,
        previous_block_projects: (previousBlock?.projects || [])
            .slice(-2)
            .map(project => toCurriculumProjectSummary(project, previousBlock.name)),
        next_block_projects: (nextBlock?.projects || [])
            .slice(0, 2)
            .map(project => toCurriculumProjectSummary(project, nextBlock.name)),
        sjm_context: currentProject.sjm || null,
        expert_development_notes: currentProject.expert_notes || null,
        additional_materials: currentProject.additional_materials || null
    };
}

function toCurriculumProjectSummary(project, blockName) {
    return {
        order: project.order,
        title: project.title,
        description: project.description,
        learning_outcomes: project.learning_outcomes || [],
        block_name: blockName || project.block_name
    };
}

function getCheckerCurriculumPayload() {
    if (!checkerCurriculum || !checkerSelectedBlock || !checkerSelectedProject || !checkerCurriculumContext) {
        return null;
    }
    return {
        curriculum_project: {
            block: checkerSelectedBlock,
            project: checkerSelectedProject
        },
        curriculum_context: checkerCurriculumContext
    };
}

function getCheckerCurriculumContext() {
    return checkerCurriculumContext;
}

window.handleCheckerCurriculumUpload = handleCheckerCurriculumUpload;
window.populateCheckerCurriculumBlocks = populateCheckerCurriculumBlocks;
window.onCheckerCurriculumBlockChange = onCheckerCurriculumBlockChange;
window.onCheckerCurriculumProjectChange = onCheckerCurriculumProjectChange;
window.buildCheckerCurriculumContext = buildCheckerCurriculumContext;
window.getCheckerCurriculumPayload = getCheckerCurriculumPayload;
window.getCheckerCurriculumContext = getCheckerCurriculumContext;
