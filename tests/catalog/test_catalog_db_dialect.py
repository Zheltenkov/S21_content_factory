"""Тесты диалектного переводчика плейсхолдеров и адаптеров write-SQL."""

from content_factory.catalog.db.dialect import (
    adapt_write_sql,
    is_pragma,
    translate_group_concat,
    translate_like,
    translate_placeholders,
)


def test_translates_qmark_to_pyformat() -> None:
    assert translate_placeholders("SELECT * FROM t WHERE id = ?") == "SELECT * FROM t WHERE id = %s"


def test_translates_multiple_placeholders() -> None:
    assert (
        translate_placeholders("INSERT INTO t (a, b) VALUES (?, ?)")
        == "INSERT INTO t (a, b) VALUES (%s, %s)"
    )


def test_ignores_qmark_inside_string_literal() -> None:
    sql = "SELECT * FROM t WHERE name = ? AND note = 'why? really'"
    assert translate_placeholders(sql) == "SELECT * FROM t WHERE name = %s AND note = 'why? really'"


def test_ignores_qmark_inside_double_quotes() -> None:
    sql = 'SELECT "col?" FROM t WHERE id = ?'
    assert translate_placeholders(sql) == 'SELECT "col?" FROM t WHERE id = %s'


def test_no_placeholders_unchanged() -> None:
    sql = "SELECT count(*) FROM catalog.skill WHERE status = 'active'"
    assert translate_placeholders(sql) == sql


def test_is_pragma() -> None:
    assert is_pragma("PRAGMA foreign_keys=ON")
    assert is_pragma("  pragma table_info(skill)")
    assert not is_pragma("SELECT 1")


def test_adapt_insert_appends_returning_id() -> None:
    sql, wants = adapt_write_sql("INSERT INTO t (a) VALUES (?)")
    assert wants is True
    assert sql == "INSERT INTO t (a) VALUES (?) RETURNING id"


def test_adapt_insert_with_existing_returning_untouched() -> None:
    sql, wants = adapt_write_sql("INSERT INTO t (a) VALUES (?) RETURNING id")
    assert wants is False
    assert sql == "INSERT INTO t (a) VALUES (?) RETURNING id"


def test_adapt_insert_or_ignore_to_on_conflict() -> None:
    sql, wants = adapt_write_sql("INSERT OR IGNORE INTO t (a) VALUES (?)")
    # ON CONFLICT-запросы не получают RETURNING id (upsert по ключу, id не нужен).
    assert wants is False
    assert sql == "INSERT INTO t (a) VALUES (?) ON CONFLICT DO NOTHING"


def test_adapt_on_conflict_upsert_no_returning() -> None:
    """Upsert над id-less таблицей (evidence_query_cache) не должен получить RETURNING id."""
    sql, wants = adapt_write_sql(
        "INSERT INTO evidence_query_cache (cache_key) VALUES (?) "
        "ON CONFLICT(cache_key) DO UPDATE SET model = excluded.model"
    )
    assert wants is False
    assert "RETURNING" not in sql


def test_adapt_strips_trailing_semicolon() -> None:
    sql, _ = adapt_write_sql("INSERT INTO t (a) VALUES (?);")
    assert sql == "INSERT INTO t (a) VALUES (?) RETURNING id"


def test_adapt_select_unchanged() -> None:
    sql, wants = adapt_write_sql("SELECT * FROM t WHERE id = ?")
    assert wants is False
    assert sql == "SELECT * FROM t WHERE id = ?"


def test_group_concat_text_to_string_agg() -> None:
    assert (
        translate_group_concat("SELECT GROUP_CONCAT(DISTINCT p.name) AS x FROM t")
        == "SELECT string_agg(DISTINCT p.name::text, ',') AS x FROM t"
    )


def test_group_concat_numeric_cast_to_text() -> None:
    assert (
        translate_group_concat("GROUP_CONCAT(DISTINCT cs.skill_id)")
        == "string_agg(DISTINCT cs.skill_id::text, ',')"
    )


def test_group_concat_no_match_unchanged() -> None:
    sql = "SELECT count(*) FROM t"
    assert translate_group_concat(sql) == sql


def test_like_to_ilike_operator() -> None:
    assert (
        translate_like("SELECT * FROM t WHERE title LIKE ?")
        == "SELECT * FROM t WHERE title ILIKE ?"
    )


def test_like_not_translated_inside_string_literal() -> None:
    # 'brief:%' — литерал; слова LIKE в нём нет, но проверяем, что литерал не трогается.
    sql = "SELECT * FROM t WHERE source_ref LIKE 'brief:%'"
    assert translate_like(sql) == "SELECT * FROM t WHERE source_ref ILIKE 'brief:%'"


def test_like_token_inside_literal_untouched() -> None:
    sql = "SELECT * FROM t WHERE note = 'I LIKE cats' AND name LIKE ?"
    assert translate_like(sql) == "SELECT * FROM t WHERE note = 'I LIKE cats' AND name ILIKE ?"


def test_not_like_becomes_not_ilike() -> None:
    assert translate_like("WHERE x NOT LIKE ?") == "WHERE x NOT ILIKE ?"
