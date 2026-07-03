-- PostgreSQL DDL for the curriculum-plan (УП) relational mirror (schema `catalog`).
-- Phase 4c (B-lite): replaces the lossy JSON-blob SpravochnikCatalogEntity mirror with
-- real relational tables the generator reads directly. Authoring stays in the SQLite
-- intake pipeline (4b hybrid); a structured sync mirrors plans here faithfully.
--
-- brief_id / profile_id are SOFT references (their parents — profile_brief, catalog.profile
-- for the intake profile — live in the SQLite intake layer), so no FK here; the value is
-- copied verbatim. curriculum_plan_row.plan_id FKs the mirror plan (both in Postgres).
-- Applied by migrations/versions/015_curriculum_plan_mirror.py.

CREATE TABLE IF NOT EXISTS catalog.curriculum_plan (
    id integer PRIMARY KEY,
    brief_id integer,
    source_policy text NOT NULL DEFAULT 'accepted_only',
    status text NOT NULL DEFAULT 'draft',
    title text,
    audience_level text,
    total_blocks integer NOT NULL DEFAULT 0,
    total_projects integer NOT NULL DEFAULT 0,
    total_hours double precision NOT NULL DEFAULT 0,
    total_days double precision NOT NULL DEFAULT 0,
    total_xp integer NOT NULL DEFAULT 0,
    payload_json text,
    created_at text NOT NULL,
    updated_at text NOT NULL,
    profile_id integer,
    direction text NOT NULL DEFAULT '',
    version text NOT NULL DEFAULT 'v1',
    author_ref text,
    metadata_json jsonb NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS catalog.curriculum_plan_row (
    id integer PRIMARY KEY,
    plan_id integer NOT NULL REFERENCES catalog.curriculum_plan(id) ON DELETE CASCADE,
    block_index integer NOT NULL,
    row_number integer NOT NULL,
    project_index_in_block integer NOT NULL,
    block_title text,
    block_goal text,
    project_name text NOT NULL,
    project_summary text,
    outcomes_know text,
    outcomes_can text,
    outcomes_skills text,
    learning_outcomes text,
    skills_list text,
    audience_level text,
    required_tools text,
    materials text,
    storytelling text,
    delivery_format text,
    group_size text,
    effort_hours double precision,
    effort_days double precision,
    cumulative_days double precision,
    xp integer,
    platform_project_name text,
    artifact_links text,
    completion_percent double precision,
    p2p_checks integer,
    weighted_skills text,
    validation_criteria text
);

CREATE INDEX IF NOT EXISTS idx_curriculum_plan_brief ON catalog.curriculum_plan (brief_id);

CREATE INDEX IF NOT EXISTS idx_curriculum_plan_row_plan ON catalog.curriculum_plan_row (plan_id, block_index, row_number);
