-- PostgreSQL compatibility functions for the catalog (schema `catalog`).
-- Mirror SQLite built-ins the catalog SQL relies on. Applied WHOLE (not split) by
-- migrations/versions/016_catalog_working_tables.py — the plpgsql bodies contain ';'.

-- Deterministic search normalization (mirror of viewer.app.normalize_search_text:
-- casefold + ё→е + collapse whitespace). Replaces the SQLite create_function shim.
CREATE OR REPLACE FUNCTION catalog.search_norm(value text) RETURNS text AS
$func$
    SELECT regexp_replace(btrim(replace(lower(value), 'ё', 'е')), '\s+', ' ', 'g')
$func$ LANGUAGE sql IMMUTABLE;

-- SQLite json_valid(x): 1 if x parses as JSON, else 0. Here boolean (used in WHERE/AND).
CREATE OR REPLACE FUNCTION catalog.json_valid(value text) RETURNS boolean AS
$func$
BEGIN
    IF value IS NULL THEN
        RETURN false;
    END IF;
    PERFORM value::jsonb;
    RETURN true;
EXCEPTION WHEN others THEN
    RETURN false;
END;
$func$ LANGUAGE plpgsql IMMUTABLE;

-- SQLite json_extract(x, '$.a.b'): value at path. Supports simple dotted object paths
-- ('$' / '$.field' / '$.a.b'); array indexes are not used by the catalog. Returns text
-- (matches how the catalog compares the result); invalid JSON / missing path → NULL.
CREATE OR REPLACE FUNCTION catalog.json_extract(value text, path text) RETURNS text AS
$func$
DECLARE
    keys text[];
BEGIN
    IF value IS NULL OR path IS NULL THEN
        RETURN NULL;
    END IF;
    keys := string_to_array(regexp_replace(path, '^\$\.?', ''), '.');
    IF keys IS NULL OR keys = ARRAY['']::text[] THEN
        RETURN value::jsonb #>> '{}';
    END IF;
    RETURN value::jsonb #>> keys;
EXCEPTION WHEN others THEN
    RETURN NULL;
END;
$func$ LANGUAGE plpgsql IMMUTABLE;
