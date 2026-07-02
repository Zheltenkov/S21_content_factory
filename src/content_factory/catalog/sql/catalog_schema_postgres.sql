-- PostgreSQL DDL for the canonical catalog (schema `catalog`).
-- Generated from catalog_schema.sql (SQLite) during the Phase-4b hybrid merge.
-- Applied by migrations/versions/014_catalog_schema.py.

CREATE SCHEMA IF NOT EXISTS catalog;

CREATE TABLE IF NOT EXISTS catalog.ingest_run (
    id integer PRIMARY KEY,
    started_at text NOT NULL,
    finished_at text,
    source_root text NOT NULL,
    status text NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    summary_json text
);

CREATE TABLE IF NOT EXISTS catalog.source_workbook (
    id integer PRIMARY KEY,
    ingest_run_id integer NOT NULL REFERENCES catalog.ingest_run(id) ON DELETE CASCADE,
    file_path text NOT NULL,
    file_name text NOT NULL,
    sha256 text NOT NULL,
    last_modified_utc text,
    source_kind text NOT NULL CHECK (source_kind IN ('role_profile', 'template', 'draft', 'reference')),
    UNIQUE (ingest_run_id, file_path)
);

CREATE TABLE IF NOT EXISTS catalog.source_sheet (
    id integer PRIMARY KEY,
    source_workbook_id integer NOT NULL REFERENCES catalog.source_workbook(id) ON DELETE CASCADE,
    sheet_name text NOT NULL,
    sheet_order integer NOT NULL,
    is_skipped integer NOT NULL DEFAULT 0 CHECK (is_skipped IN (0, 1)),
    skip_reason text,
    UNIQUE (source_workbook_id, sheet_order)
);

CREATE TABLE IF NOT EXISTS catalog.source_block (
    id integer PRIMARY KEY,
    source_sheet_id integer NOT NULL REFERENCES catalog.source_sheet(id) ON DELETE CASCADE,
    block_no integer NOT NULL,
    header_row_number integer NOT NULL,
    level_row_number integer,
    end_row_number integer,
    raw_title text,
    raw_description text,
    raw_prerequisites text,
    raw_scale_signature text,
    UNIQUE (source_sheet_id, block_no)
);

CREATE TABLE IF NOT EXISTS catalog.profile (
    id integer PRIMARY KEY,
    slug text NOT NULL UNIQUE,
    name text NOT NULL,
    source_kind text NOT NULL CHECK (source_kind IN ('role_profile', 'template', 'draft', 'reference')),
    notes text
);

CREATE TABLE IF NOT EXISTS catalog.profile_source (
    id integer PRIMARY KEY,
    profile_id integer NOT NULL REFERENCES catalog.profile(id) ON DELETE CASCADE,
    source_workbook_id integer NOT NULL REFERENCES catalog.source_workbook(id) ON DELETE CASCADE,
    version_label text,
    is_primary integer NOT NULL DEFAULT 1 CHECK (is_primary IN (0, 1)),
    UNIQUE (profile_id, source_workbook_id)
);

CREATE TABLE IF NOT EXISTS catalog.proficiency_scale (
    id integer PRIMARY KEY,
    code text NOT NULL UNIQUE,
    title text NOT NULL,
    normalized_signature text NOT NULL UNIQUE,
    description text
);

CREATE TABLE IF NOT EXISTS catalog.proficiency_level (
    id integer PRIMARY KEY,
    scale_id integer NOT NULL REFERENCES catalog.proficiency_scale(id) ON DELETE CASCADE,
    code text NOT NULL,
    title text NOT NULL,
    sort_order integer NOT NULL,
    canonical_band text,
    UNIQUE (scale_id, code),
    UNIQUE (scale_id, title)
);

CREATE TABLE IF NOT EXISTS catalog.dimension (
    id integer PRIMARY KEY,
    code text NOT NULL UNIQUE,
    title text NOT NULL
);

CREATE TABLE IF NOT EXISTS catalog.competency (
    id integer PRIMARY KEY,
    normalized_title text NOT NULL UNIQUE,
    title text NOT NULL,
    description text,
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'candidate', 'deprecated'))
);

CREATE TABLE IF NOT EXISTS catalog.typed_competency (
    id integer PRIMARY KEY,
    normalized_name text NOT NULL UNIQUE,
    name text NOT NULL UNIQUE,
    sort_order integer NOT NULL,
    source text NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'live_snapshot', 'derived')),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'candidate', 'deprecated'))
);

CREATE TABLE IF NOT EXISTS catalog.profile_competency (
    id integer PRIMARY KEY,
    profile_id integer NOT NULL REFERENCES catalog.profile(id) ON DELETE CASCADE,
    competency_id integer NOT NULL REFERENCES catalog.competency(id) ON DELETE CASCADE,
    source_block_id integer NOT NULL REFERENCES catalog.source_block(id) ON DELETE CASCADE,
    scale_id integer REFERENCES catalog.proficiency_scale(id) ON DELETE SET NULL,
    title_in_source text,
    description_in_source text,
    prerequisites_text text,
    sort_order integer NOT NULL,
    review_state text NOT NULL DEFAULT 'accepted' CHECK (review_state IN ('accepted', 'needs_review', 'draft')),
    UNIQUE (profile_id, source_block_id)
);

CREATE TABLE IF NOT EXISTS catalog.skill (
    id integer PRIMARY KEY,
    normalized_name text NOT NULL UNIQUE,
    canonical_name text NOT NULL,
    skill_type text NOT NULL DEFAULT 'unknown' CHECK (skill_type IN ('hard', 'soft', 'domain', 'tool', 'process', 'unknown')),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'candidate', 'deprecated'))
);

CREATE TABLE IF NOT EXISTS catalog.skill_alias (
    id integer PRIMARY KEY,
    skill_id integer NOT NULL REFERENCES catalog.skill(id) ON DELETE CASCADE,
    alias text NOT NULL,
    normalized_alias text NOT NULL,
    source text,
    UNIQUE (skill_id, normalized_alias)
);

CREATE TABLE IF NOT EXISTS catalog.competency_skill (
    id integer PRIMARY KEY,
    profile_competency_id integer NOT NULL REFERENCES catalog.profile_competency(id) ON DELETE CASCADE,
    skill_id integer REFERENCES catalog.skill(id) ON DELETE SET NULL,
    source_skill_name text NOT NULL,
    skill_order integer NOT NULL,
    review_state text NOT NULL DEFAULT 'accepted' CHECK (review_state IN ('accepted', 'needs_review', 'draft')),
    UNIQUE (profile_competency_id, skill_order)
);

CREATE TABLE IF NOT EXISTS catalog.typed_competency_skill (
    id integer PRIMARY KEY,
    typed_competency_id integer NOT NULL REFERENCES catalog.typed_competency(id) ON DELETE CASCADE,
    skill_id integer REFERENCES catalog.skill(id) ON DELETE SET NULL,
    source_skill_name text NOT NULL,
    sort_order integer NOT NULL,
    resolution_status text NOT NULL DEFAULT 'matched' CHECK (resolution_status IN ('matched', 'alias', 'manual', 'fuzzy', 'missing')),
    match_note text,
    source text NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'live_snapshot', 'derived')),
    UNIQUE (typed_competency_id, source_skill_name)
);

CREATE TABLE IF NOT EXISTS catalog.indicator_row (
    id integer PRIMARY KEY,
    competency_skill_id integer NOT NULL REFERENCES catalog.competency_skill(id) ON DELETE CASCADE,
    dimension_id integer NOT NULL REFERENCES catalog.dimension(id) ON DELETE RESTRICT,
    source_row_number integer NOT NULL,
    inherited_skill integer NOT NULL DEFAULT 0 CHECK (inherited_skill IN (0, 1)),
    inherited_dimension integer NOT NULL DEFAULT 0 CHECK (inherited_dimension IN (0, 1)),
    base_text text,
    raw_number text,
    notes text,
    UNIQUE (competency_skill_id, source_row_number)
);

CREATE TABLE IF NOT EXISTS catalog.indicator_row_meta (
    id integer PRIMARY KEY,
    indicator_row_id integer NOT NULL REFERENCES catalog.indicator_row(id) ON DELETE CASCADE,
    meta_key text NOT NULL,
    meta_value text NOT NULL,
    UNIQUE (indicator_row_id, meta_key)
);

CREATE TABLE IF NOT EXISTS catalog.indicator_level_cell (
    id integer PRIMARY KEY,
    indicator_row_id integer NOT NULL REFERENCES catalog.indicator_row(id) ON DELETE CASCADE,
    proficiency_level_id integer REFERENCES catalog.proficiency_level(id) ON DELETE SET NULL,
    raw_level_label text NOT NULL,
    raw_value text NOT NULL,
    value_kind text NOT NULL CHECK (value_kind IN ('text', 'marker_plus', 'marker_minus', 'numeric', 'blank')),
    sort_order integer NOT NULL,
    UNIQUE (indicator_row_id, raw_level_label, sort_order)
);

CREATE TABLE IF NOT EXISTS catalog.taxonomy_node (
    id integer PRIMARY KEY,
    normalized_name text NOT NULL UNIQUE,
    name text NOT NULL,
    node_type text NOT NULL CHECK (node_type IN ('domain', 'topic', 'tool', 'method', 'concept', 'role')),
    parent_id integer REFERENCES catalog.taxonomy_node(id) ON DELETE SET NULL,
    description text
);

CREATE TABLE IF NOT EXISTS catalog.taxonomy_edge (
    id integer PRIMARY KEY,
    from_node_id integer NOT NULL REFERENCES catalog.taxonomy_node(id) ON DELETE CASCADE,
    to_node_id integer NOT NULL REFERENCES catalog.taxonomy_node(id) ON DELETE CASCADE,
    relation_type text NOT NULL CHECK (relation_type IN ('parent_of', 'depends_on', 'related_to', 'uses', 'part_of')),
    weight double precision,
    UNIQUE (from_node_id, to_node_id, relation_type)
);

CREATE TABLE IF NOT EXISTS catalog.skill_taxonomy (
    skill_id integer NOT NULL REFERENCES catalog.skill(id) ON DELETE CASCADE,
    taxonomy_node_id integer NOT NULL REFERENCES catalog.taxonomy_node(id) ON DELETE CASCADE,
    relation_type text NOT NULL CHECK (relation_type IN ('belongs_to', 'depends_on', 'uses', 'recommended_for')),
    PRIMARY KEY (skill_id, taxonomy_node_id, relation_type)
);

CREATE TABLE IF NOT EXISTS catalog.course (
    id integer PRIMARY KEY,
    code text NOT NULL UNIQUE,
    title text NOT NULL,
    description text
);

CREATE TABLE IF NOT EXISTS catalog.project (
    id integer PRIMARY KEY,
    external_id text,
    code text NOT NULL UNIQUE,
    title text NOT NULL,
    course_id integer REFERENCES catalog.course(id) ON DELETE SET NULL,
    description text,
    repo_url text,
    time_hours integer,
    is_active integer NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    created_at text NOT NULL,
    updated_at text
);

CREATE TABLE IF NOT EXISTS catalog.project_indicator (
    id integer PRIMARY KEY,
    project_id integer NOT NULL REFERENCES catalog.project(id) ON DELETE CASCADE,
    indicator_level_cell_id integer REFERENCES catalog.indicator_level_cell(id) ON DELETE SET NULL,
    indicator_row_id integer REFERENCES catalog.indicator_row(id) ON DELETE SET NULL,
    source text NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'ai_chatgpt', 'ai_deepseek', 'import')),
    confidence double precision,
    note text,
    created_at text NOT NULL
);

CREATE TABLE IF NOT EXISTS catalog.ai_analysis_run (
    id integer PRIMARY KEY,
    project_id integer NOT NULL REFERENCES catalog.project(id) ON DELETE CASCADE,
    provider text NOT NULL CHECK (provider IN ('chatgpt', 'deepseek', 'other')),
    status text NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    started_at text NOT NULL,
    finished_at text,
    prompt_version text,
    raw_output text,
    summary text
);

CREATE TABLE IF NOT EXISTS catalog.ai_analysis_suggestion (
    id integer PRIMARY KEY,
    run_id integer NOT NULL REFERENCES catalog.ai_analysis_run(id) ON DELETE CASCADE,
    project_indicator_id integer REFERENCES catalog.project_indicator(id) ON DELETE SET NULL,
    competency_id integer REFERENCES catalog.competency(id) ON DELETE SET NULL,
    skill_id integer REFERENCES catalog.skill(id) ON DELETE SET NULL,
    indicator_row_id integer REFERENCES catalog.indicator_row(id) ON DELETE SET NULL,
    suggested_text text,
    suggested_dimension text,
    rationale text,
    confidence double precision,
    decision text NOT NULL DEFAULT 'pending' CHECK (decision IN ('pending', 'accepted', 'rejected')),
    UNIQUE (run_id, indicator_row_id, suggested_text)
);

CREATE TABLE IF NOT EXISTS catalog.review_queue (
    id integer PRIMARY KEY,
    entity_type text NOT NULL CHECK (entity_type IN ('workbook', 'sheet', 'block', 'competency', 'skill', 'indicator_row', 'profile', 'project', 'project_indicator', 'ai_analysis_run', 'prerequisite_edge')),
    entity_id integer,
    source_ref text,
    reason_code text NOT NULL,
    severity text NOT NULL CHECK (severity IN ('info', 'warning', 'error')),
    details text,
    status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'ignored')),
    resolution_note text,
    reviewed_at text,
    updated_at text,
    created_at text NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_source_sheet_workbook ON catalog.source_sheet (source_workbook_id, is_skipped);

CREATE INDEX IF NOT EXISTS idx_source_block_sheet ON catalog.source_block (source_sheet_id, block_no);

CREATE INDEX IF NOT EXISTS idx_typed_competency_order ON catalog.typed_competency (sort_order, name);

CREATE INDEX IF NOT EXISTS idx_profile_competency_profile ON catalog.profile_competency (profile_id, sort_order);

CREATE INDEX IF NOT EXISTS idx_profile_competency_competency ON catalog.profile_competency (competency_id);

CREATE INDEX IF NOT EXISTS idx_skill_alias_normalized ON catalog.skill_alias (normalized_alias);

CREATE INDEX IF NOT EXISTS idx_competency_skill_profile_competency ON catalog.competency_skill (profile_competency_id, skill_order);

CREATE INDEX IF NOT EXISTS idx_competency_skill_skill ON catalog.competency_skill (skill_id);

CREATE INDEX IF NOT EXISTS idx_typed_competency_skill_typed_competency ON catalog.typed_competency_skill (typed_competency_id, sort_order);

CREATE INDEX IF NOT EXISTS idx_typed_competency_skill_skill ON catalog.typed_competency_skill (skill_id, resolution_status);

CREATE INDEX IF NOT EXISTS idx_indicator_row_skill ON catalog.indicator_row (competency_skill_id, source_row_number);

CREATE INDEX IF NOT EXISTS idx_indicator_level_row ON catalog.indicator_level_cell (indicator_row_id, sort_order);

CREATE INDEX IF NOT EXISTS idx_project_course ON catalog.project (course_id, is_active);

CREATE INDEX IF NOT EXISTS idx_project_indicator_project ON catalog.project_indicator (project_id, source);

CREATE INDEX IF NOT EXISTS idx_project_indicator_indicator_row ON catalog.project_indicator (indicator_row_id);

CREATE INDEX IF NOT EXISTS idx_ai_analysis_run_project ON catalog.ai_analysis_run (project_id, provider, status);

CREATE INDEX IF NOT EXISTS idx_ai_analysis_suggestion_run ON catalog.ai_analysis_suggestion (run_id, decision);

CREATE INDEX IF NOT EXISTS idx_review_queue_status ON catalog.review_queue (status, severity, reason_code);

CREATE OR REPLACE VIEW catalog.v_skill_usage AS
SELECT
    s.id AS skill_id,
    s.canonical_name,
    s.normalized_name,
    COUNT(DISTINCT cs.profile_competency_id) AS competency_links,
    COUNT(DISTINCT pc.profile_id) AS profile_count
FROM catalog.skill s
JOIN catalog.competency_skill cs ON cs.skill_id = s.id
JOIN catalog.profile_competency pc ON pc.id = cs.profile_competency_id
GROUP BY s.id, s.canonical_name, s.normalized_name;

CREATE OR REPLACE VIEW catalog.v_pending_reviews AS
SELECT
    id,
    entity_type,
    entity_id,
    source_ref,
    reason_code,
    severity,
    details,
    status,
    created_at
FROM catalog.review_queue
WHERE status = 'open';
