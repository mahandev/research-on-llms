import sqlite3

import pytest

from bsebot import db


EXPECTED_TABLES = {
    "raw_documents",
    "facts",
    "agent_runs",
    "trades",
    "agent_memory",
    "alerts",
    "tools",
    "harvester_runs",
    "llm_call_log",
    "cash_ledger",
    "price_history",
    "_migrations",
}


def test_connect_enables_wal_and_foreign_keys(tmp_db_path, migrations_dir):
    db.run_migrations(tmp_db_path, migrations_dir)
    conn = db.connect(tmp_db_path)
    try:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    finally:
        conn.close()
    assert journal_mode.lower() == "wal"
    assert fk == 1


def test_run_migrations_creates_all_tables(tmp_db_path, migrations_dir):
    db.run_migrations(tmp_db_path, migrations_dir)
    conn = db.connect(tmp_db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    finally:
        conn.close()
    names = {r[0] for r in rows}
    assert EXPECTED_TABLES.issubset(names)


def test_run_migrations_is_idempotent(tmp_db_path, migrations_dir):
    db.run_migrations(tmp_db_path, migrations_dir)
    db.run_migrations(tmp_db_path, migrations_dir)  # must not raise
    conn = db.connect(tmp_db_path)
    try:
        rows = conn.execute(
            "SELECT filename FROM _migrations ORDER BY filename"
        ).fetchall()
    finally:
        conn.close()
    assert [r[0] for r in rows] == ["001_initial.sql"]


def test_foreign_keys_are_enforced(tmp_db_path, migrations_dir):
    db.run_migrations(tmp_db_path, migrations_dir)
    conn = db.connect(tmp_db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO facts (source_doc_id, fact_type, payload_json, source_quote) "
                "VALUES (99999, 'x', '{}', 'q')"
            )
            conn.commit()
    finally:
        conn.close()


def test_table_stats_returns_counts(tmp_db_path, migrations_dir):
    db.run_migrations(tmp_db_path, migrations_dir)
    stats = db.table_stats(tmp_db_path)
    assert "raw_documents" in stats
    assert stats["raw_documents"] == 0
    assert "facts" in stats
