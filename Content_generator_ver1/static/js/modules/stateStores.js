// Central state containers for generator/result/regeneration UI.
// The legacy window accessors are temporary compatibility shims for inline handlers
// and older modules that are still being migrated.

(function initContentGenStores() {
    if (window.ContentGenStores) return;

    function cloneInitialState(initialState) {
        return { ...(initialState || {}) };
    }

    function createStore(initialState = {}) {
        let state = cloneInitialState(initialState);
        const subscribers = new Set();

        function getState() {
            return { ...state };
        }

        function setState(updates = {}) {
            const patch = updates && typeof updates === 'object' ? updates : {};
            let changed = false;
            const next = { ...state };
            Object.entries(patch).forEach(([key, value]) => {
                if (!Object.is(next[key], value)) {
                    next[key] = value;
                    changed = true;
                }
            });
            if (!changed) return getState();
            const previous = state;
            state = next;
            subscribers.forEach((subscriber) => {
                try {
                    subscriber(getState(), { ...previous });
                } catch (error) {
                    console.error('ContentGen store subscriber failed:', error);
                }
            });
            return getState();
        }

        function reset(nextInitialState = initialState) {
            state = cloneInitialState(nextInitialState);
            subscribers.forEach((subscriber) => {
                try {
                    subscriber(getState(), {});
                } catch (error) {
                    console.error('ContentGen store subscriber failed:', error);
                }
            });
            return getState();
        }

        function subscribe(subscriber) {
            if (typeof subscriber !== 'function') return () => {};
            subscribers.add(subscriber);
            return () => subscribers.delete(subscriber);
        }

        return { getState, setState, reset, subscribe };
    }

    const STANDARD_WORKFLOW_PROFILE = {
        id: 'standard',
        title: 'Обычный режим',
        description: 'Автоматическая генерация без ручных контрольных точек между этапами.',
        stages: [
            'context',
            'task_planning',
            'title_annotation',
            'skeleton',
            'theory',
            'practice',
            'global_quality',
            'evaluation',
            'finalize'
        ],
        gates: [],
        capabilities: {
            project_regeneration: true,
            section_regeneration: true,
            methodology_assistant: false,
            stage_review: false,
            final_readme_editing: true,
            checklist_editing: true
        }
    };

    const METHODOLOGY_WORKFLOW_PROFILE = {
        id: 'methodology',
        title: 'Методологический режим',
        description: 'Генерация с контрольными точками методолога между этапами.',
        stages: [...STANDARD_WORKFLOW_PROFILE.stages],
        gates: STANDARD_WORKFLOW_PROFILE.stages
            .filter(stage => stage !== 'finalize')
            .map(stage => ({ after_stage: stage, action: 'approve_or_revise' })),
        capabilities: {
            project_regeneration: true,
            section_regeneration: true,
            methodology_assistant: true,
            stage_review: true,
            final_readme_editing: true,
            checklist_editing: true
        }
    };

    function normalizeWorkflowProfile(profile) {
        if (profile && typeof profile === 'object') {
            const base = profile.id === 'methodology' ? METHODOLOGY_WORKFLOW_PROFILE : STANDARD_WORKFLOW_PROFILE;
            const capabilities = {
                ...base.capabilities,
                ...(profile.capabilities || {})
            };
            if (base.id === 'methodology') {
                capabilities.project_regeneration = true;
                capabilities.section_regeneration = true;
            }
            return {
                ...base,
                ...profile,
                capabilities,
                gates: Array.isArray(profile.gates) ? profile.gates : base.gates,
                stages: Array.isArray(profile.stages) ? profile.stages : base.stages
            };
        }
        return profile === 'methodology' ? METHODOLOGY_WORKFLOW_PROFILE : STANDARD_WORKFLOW_PROFILE;
    }

    const workflowProfileStore = createStore({
        profile: STANDARD_WORKFLOW_PROFILE,
        profileId: STANDARD_WORKFLOW_PROFILE.id,
        capabilities: STANDARD_WORKFLOW_PROFILE.capabilities,
    });

    const generationStore = createStore({
        currentRequestId: null,
        currentMarkdown: null,
        originalMarkdown: null,
        currentTranslatedMarkdown: null,
        currentResult: null,
        currentSeed: null,
        generationStartTime: null,
        lastKnownGenerationPhase: null,
        lastKnownGenerationProgress: 0,
        lastKnownGenerationAgent: 'Инициализация...',
        currentGenerationStatus: 'idle',
        lastGenerationError: null,
    });

    const resultStore = createStore({
        originalRubric: null,
        originalTextStats: null,
        regeneratedRubric: null,
        regeneratedTextStats: null,
        regeneratedMarkdown: null,
        currentRubric: null,
        currentMetricsVersion: 'original',
        currentReportVersion: 'original',
        currentFilter: 'all',
        readmeRenderMode: 'preview',
        readmeComparisonActive: false,
    });

    const regenerationStore = createStore({
        sections: [],
        sectionDrafts: {},
        instructionHistory: [],
        selectedInstructions: [],
        validationReport: null,
        warnings: [],
        accepted: true,
        rubricRegression: null,
    });

    function defineLegacyAccessor(name, store, key) {
        const descriptor = Object.getOwnPropertyDescriptor(window, name);
        if (descriptor && descriptor.configurable === false) return;
        Object.defineProperty(window, name, {
            configurable: true,
            enumerable: true,
            get() {
                return store.getState()[key];
            },
            set(value) {
                store.setState({ [key]: value });
            },
        });
    }

    function bindLegacyWindowState() {
        defineLegacyAccessor('currentResult', generationStore, 'currentResult');
        defineLegacyAccessor('__contentGenCurrentResult', generationStore, 'currentResult');
        defineLegacyAccessor('regeneratedMarkdown', resultStore, 'regeneratedMarkdown');
        defineLegacyAccessor('currentRubric', resultStore, 'currentRubric');
        defineLegacyAccessor('regeneratedRubric', resultStore, 'regeneratedRubric');
        defineLegacyAccessor('regeneratedTextStats', resultStore, 'regeneratedTextStats');
        defineLegacyAccessor('currentMetricsVersion', resultStore, 'currentMetricsVersion');
        defineLegacyAccessor('currentReportVersion', resultStore, 'currentReportVersion');
        defineLegacyAccessor('currentFilter', resultStore, 'currentFilter');
    }

    function getState() {
        const workflowProfileState = workflowProfileStore.getState();
        return {
            ...generationStore.getState(),
            ...resultStore.getState(),
            regeneration: regenerationStore.getState(),
            workflowProfile: workflowProfileState.profile,
            workflowCapabilities: workflowProfileState.capabilities,
        };
    }

    function setState(updates = {}) {
        const generationKeys = new Set(Object.keys(generationStore.getState()));
        const resultKeys = new Set(Object.keys(resultStore.getState()));
        const generationUpdates = {};
        const resultUpdates = {};
        Object.entries(updates || {}).forEach(([key, value]) => {
            if (generationKeys.has(key)) generationUpdates[key] = value;
            if (resultKeys.has(key)) resultUpdates[key] = value;
        });
        if (Object.keys(generationUpdates).length) generationStore.setState(generationUpdates);
        if (Object.keys(resultUpdates).length) resultStore.setState(resultUpdates);
        if (updates.regeneration && typeof updates.regeneration === 'object') {
        regenerationStore.setState(updates.regeneration);
        }
        const workflowProfilePatch = updates.workflowProfile || updates.workflow_profile || null;
        if (workflowProfilePatch) {
            const profile = normalizeWorkflowProfile(workflowProfilePatch);
            workflowProfileStore.setState({
                profile,
                profileId: profile.id,
                capabilities: profile.capabilities,
            });
        }
        return getState();
    }

    window.ContentGenStores = {
        createStore,
        normalizeWorkflowProfile,
        STANDARD_WORKFLOW_PROFILE,
        METHODOLOGY_WORKFLOW_PROFILE,
        generationStore,
        resultStore,
        regenerationStore,
        workflowProfileStore,
        bindLegacyWindowState,
        getState,
        setState,
    };

    bindLegacyWindowState();
})();
