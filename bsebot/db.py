"""SQLite connection helper, WAL setup, and migration runner."""

from __future__ import annotations

import sqlite3
from pathlib import Path


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode and foreign keys enforced."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _migrations ("
        "  filename TEXT PRIMARY KEY,"
        "  applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
        ")"
    )
    conn.commit()


def run_migrations(db_path: str | Path, migrations_dir: str | Path) -> list[str]:
    """Apply any *.sql files in migrations_dir that aren't yet recorded.

    Returns the list of filenames newly applied.
    """
    db_path = Path(db_path)
    migrations_dir = Path(migrations_dir)
    conn = connect(db_path)
    try:
        _ensure_migrations_table(conn)
        applied = {
            row[0]
            for row in conn.execute("SELECT filename FROM _migrations").fetchall()
        }
        files = sorted(p for p in migrations_dir.glob("*.sql"))
        newly_applied: list[str] = []
        for path in files:
            name = path.name
            if name in applied:
                continue
            sql = path.read_text(encoding="utf-8")
            conn.executescript(sql)
            conn.execute(
                "INSERT OR IGNORE INTO _migrations (filename) VALUES (?)", (name,)
            )
            conn.commit()
            newly_applied.append(name)
        return newly_applied
    finally:
        conn.close()


def table_stats(db_path: str | Path) -> dict[str, int]:
    """Return a dict of {table_name: row_count} for every user table."""
    conn = connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        stats: dict[str, int] = {}
        for (name,) in rows:
            count = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            stats[name] = count
        return stats
    finally:
        conn.close()


def select_only(sql: str) -> bool:
    """Cheap guard: True iff the trimmed SQL begins with SELECT and has no semicolons mid-statement."""
    cleaned = sql.strip().rstrip(";").strip()
    if ";" in cleaned:
        return False
    lowered = cleaned.lower()
    return lowered.startswith("select") or lowered.startswith("with")
