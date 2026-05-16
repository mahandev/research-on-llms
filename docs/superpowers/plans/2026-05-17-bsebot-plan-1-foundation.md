# BSEBot Plan 1 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the BSEBot project skeleton (Python package, SQLite schema, config loader), the multi-provider LLM router with truncation handling, and a minimal CLI exposing `bsebot llm test`, `bsebot db stats`, and `bsebot db query`.

**Architecture:** A single Python 3.11 package `bsebot/` with one SQLite DB (WAL, foreign keys on) initialized via append-only `.sql` migration files. A `LLMRouter` class wraps `litellm` (with `instructor` for structured output) and walks an ordered provider chain (Gemini → Cerebras → Groq → GitHub Models) on rate-limit/API errors, with a continuation pattern for `finish_reason="length"`. Every LLM call is logged to `llm_call_log`. The CLI uses `click` to expose foundation-level subcommands; subsequent plan subcommands are stubbed.

**Tech Stack:** Python 3.11, SQLite (stdlib), `litellm`, `instructor`, `pydantic>=2`, `python-dotenv`, `pyyaml`, `click`, `pytest`, `pytest-mock`.

**Scope:** Build-order items 1–2 from the spec only. Out of scope (deferred to later plans): harvesters, extractor, agent runner, alerts, position manager, vault writer, Quartz, bootstrap, reporter.

---

## File Structure

Files created in this plan (all paths relative to repo root `/Users/devamarnani/Desktop/bsebot/`):

- `pyproject.toml` — project metadata + dependencies
- `.gitignore` — ignore venv, `.env`, `data/*.db`, logs
- `.env.example` — listing required API keys
- `config.yaml` — provider chain config (example/default)
- `README.md` — quick start + how to swap providers + free-key sources
- `scripts/setup.sh` — bootstrap script
- `bsebot/__init__.py` — package marker, version
- `bsebot/db.py` — connection helper + migration runner
- `bsebot/config.py` — load YAML + `.env`
- `bsebot/llm.py` — `LLMRouter` with extract/reason + fallback + continuation + logging
- `bsebot/cli.py` — `click`-based entry point
- `migrations/001_initial.sql` — full DDL for all spec tables
- `tests/__init__.py`
- `tests/conftest.py` — shared fixtures (tmp DB, fake config)
- `tests/test_db.py`
- `tests/test_config.py`
- `tests/test_llm.py`
- `tests/test_cli.py`

---

## Task 1: Repo bootstrap (pyproject, gitignore, package marker)

**Files:**
- Create: `/Users/devamarnani/Desktop/bsebot/pyproject.toml`
- Create: `/Users/devamarnani/Desktop/bsebot/.gitignore`
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/__init__.py`
- Create: `/Users/devamarnani/Desktop/bsebot/tests/__init__.py`

- [ ] **Step 1: Create `.gitignore`**

Write `/Users/devamarnani/Desktop/bsebot/.gitignore`:

```gitignore
# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/
.pytest_cache/
.coverage
htmlcov/

# Secrets and data
.env
data/*.db
data/*.db-wal
data/*.db-shm
logs/

# OS
.DS_Store
```

- [ ] **Step 2: Create `pyproject.toml`**

Write `/Users/devamarnani/Desktop/bsebot/pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "bsebot"
version = "0.1.0"
description = "Autonomous paper-trading bot for BSE Ltd"
requires-python = ">=3.11"
dependencies = [
  "litellm>=1.50.0",
  "instructor>=1.5.0",
  "pydantic>=2.6",
  "python-dotenv>=1.0.0",
  "pyyaml>=6.0",
  "click>=8.1",
  "requests>=2.31",
  "httpx>=0.27",
  "beautifulsoup4>=4.12",
  "lxml>=5.1",
  "pdfplumber>=0.11",
  "feedparser>=6.0",
  "yfinance>=0.2.40",
  "matplotlib>=3.8",
  "python-frontmatter>=1.1",
  "apscheduler>=3.10",
]

[project.optional-dependencies]
dev = [
  "pytest>=8.0",
  "pytest-mock>=3.12",
]

[project.scripts]
bsebot = "bsebot.cli:main"

[tool.setuptools.packages.find]
include = ["bsebot*"]
exclude = ["tests*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra"
```

- [ ] **Step 3: Create package markers**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/__init__.py`:

```python
"""BSEBot — autonomous paper-trading bot for BSE Ltd."""

__version__ = "0.1.0"
```

Write `/Users/devamarnani/Desktop/bsebot/tests/__init__.py`:

```python
```

- [ ] **Step 4: Verify package imports**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -c "import bsebot; print(bsebot.__version__)"`

Expected output:
```
0.1.0
```

- [ ] **Step 5: Commit**

```bash
cd /Users/devamarnani/Desktop/bsebot
git add pyproject.toml .gitignore bsebot/__init__.py tests/__init__.py
git commit -m "feat(bsebot): bootstrap project skeleton (pyproject, package marker)"
```

---

## Task 2: SQL migration with full DDL

**Files:**
- Create: `/Users/devamarnani/Desktop/bsebot/migrations/001_initial.sql`

- [ ] **Step 1: Write `migrations/001_initial.sql`**

Write `/Users/devamarnani/Desktop/bsebot/migrations/001_initial.sql`:

```sql
-- BSEBot initial schema.
-- WAL mode and foreign_keys are turned on by the connection helper, not here.

CREATE TABLE IF NOT EXISTS raw_documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  url TEXT,
  fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  content_hash TEXT NOT NULL UNIQUE,
  content TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  processed INTEGER NOT NULL DEFAULT 0,
  processed_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_raw_documents_processed ON raw_documents(processed);
CREATE INDEX IF NOT EXISTS idx_raw_documents_source ON raw_documents(source);
CREATE INDEX IF NOT EXISTS idx_raw_documents_fetched_at ON raw_documents(fetched_at);

CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_doc_id INTEGER NOT NULL REFERENCES raw_documents(id),
  fact_type TEXT NOT NULL,
  ticker TEXT NOT NULL DEFAULT 'BSE',
  payload_json TEXT NOT NULL,
  source_quote TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1.0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_facts_doc ON facts(source_doc_id);
CREATE INDEX IF NOT EXISTS idx_facts_type ON facts(fact_type);
CREATE INDEX IF NOT EXISTS idx_facts_ticker ON facts(ticker);
CREATE INDEX IF NOT EXISTS idx_facts_created_at ON facts(created_at);

CREATE TABLE IF NOT EXISTS agent_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  ended_at TIMESTAMP,
  trigger TEXT NOT NULL,
  triggered_by_alert_id INTEGER,
  model TEXT,
  iterations INTEGER NOT NULL DEFAULT 0,
  decision_json TEXT,
  reasoning TEXT,
  fact_ids_consulted_json TEXT NOT NULL DEFAULT '[]',
  tools_called_json TEXT NOT NULL DEFAULT '[]',
  cost_usd REAL NOT NULL DEFAULT 0.0,
  status TEXT NOT NULL DEFAULT 'running'
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_started_at ON agent_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_agent_runs_status ON agent_runs(status);

CREATE TABLE IF NOT EXISTS trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  opened_by_agent_run INTEGER REFERENCES agent_runs(id),
  ticker TEXT NOT NULL DEFAULT 'BSE',
  side TEXT NOT NULL,
  quantity INTEGER NOT NULL,
  entry_price REAL NOT NULL,
  entry_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  stop_loss REAL,
  target REAL,
  force_exit_by TIMESTAMP,
  exit_price REAL,
  exit_at TIMESTAMP,
  exit_reason TEXT,
  pnl REAL,
  status TEXT NOT NULL DEFAULT 'open'
);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);

CREATE TABLE IF NOT EXISTS agent_memory (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_type TEXT NOT NULL,
  content TEXT NOT NULL,
  importance REAL NOT NULL DEFAULT 0.5,
  source_fact_ids_json TEXT NOT NULL DEFAULT '[]',
  created_by_agent_run INTEGER REFERENCES agent_runs(id),
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  superseded_by INTEGER REFERENCES agent_memory(id),
  superseded_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_agent_memory_type ON agent_memory(memory_type);
CREATE INDEX IF NOT EXISTS idx_agent_memory_active ON agent_memory(superseded_by);

CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_by_agent_run INTEGER REFERENCES agent_runs(id),
  condition TEXT NOT NULL,
  ticker TEXT NOT NULL DEFAULT 'BSE',
  threshold REAL NOT NULL,
  valid_until TIMESTAMP NOT NULL,
  why_this_threshold TEXT NOT NULL,
  source_fact_ids TEXT NOT NULL,
  linked_trade_id INTEGER REFERENCES trades(id),
  linked_thesis_id INTEGER REFERENCES agent_memory(id),
  intraday INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  fired_at TIMESTAMP,
  cooldown_until TIMESTAMP,
  fire_count INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_alerts_active ON alerts(active);
CREATE INDEX IF NOT EXISTS idx_alerts_valid_until ON alerts(valid_until);

CREATE TABLE IF NOT EXISTS tools (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  description TEXT,
  url TEXT,
  fetch_method TEXT,
  expected_signal_type TEXT,
  rationale TEXT,
  created_by TEXT NOT NULL DEFAULT 'agent',
  enabled INTEGER NOT NULL DEFAULT 0,
  approved_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS harvester_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  harvester TEXT NOT NULL,
  started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  ended_at TIMESTAMP,
  status TEXT NOT NULL,
  docs_fetched INTEGER NOT NULL DEFAULT 0,
  docs_new INTEGER NOT NULL DEFAULT 0,
  error TEXT
);
CREATE INDEX IF NOT EXISTS idx_harvester_runs_harvester ON harvester_runs(harvester);
CREATE INDEX IF NOT EXISTS idx_harvester_runs_started_at ON harvester_runs(started_at);

CREATE TABLE IF NOT EXISTS llm_call_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  called_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  role TEXT NOT NULL,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  finish_reason TEXT,
  duration_ms INTEGER NOT NULL DEFAULT 0,
  continuation_count INTEGER NOT NULL DEFAULT 0,
  cost_estimate_usd REAL NOT NULL DEFAULT 0.0,
  agent_run_id INTEGER REFERENCES agent_runs(id),
  error TEXT
);
CREATE INDEX IF NOT EXISTS idx_llm_call_log_called_at ON llm_call_log(called_at);
CREATE INDEX IF NOT EXISTS idx_llm_call_log_provider ON llm_call_log(provider);

CREATE TABLE IF NOT EXISTS cash_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  movement_type TEXT NOT NULL,
  amount REAL NOT NULL,
  related_trade_id INTEGER REFERENCES trades(id),
  note TEXT
);
CREATE INDEX IF NOT EXISTS idx_cash_ledger_occurred_at ON cash_ledger(occurred_at);
CREATE INDEX IF NOT EXISTS idx_cash_ledger_type ON cash_ledger(movement_type);

CREATE TABLE IF NOT EXISTS price_history (
  date TEXT NOT NULL,
  ticker TEXT NOT NULL DEFAULT 'BSE',
  open REAL NOT NULL,
  high REAL NOT NULL,
  low REAL NOT NULL,
  close REAL NOT NULL,
  volume INTEGER NOT NULL,
  PRIMARY KEY (date, ticker)
);
CREATE INDEX IF NOT EXISTS idx_price_history_ticker_date ON price_history(ticker, date);

CREATE TABLE IF NOT EXISTS _migrations (
  filename TEXT PRIMARY KEY,
  applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

- [ ] **Step 2: Commit migration**

```bash
cd /Users/devamarnani/Desktop/bsebot
git add migrations/001_initial.sql
git commit -m "feat(bsebot): add initial SQL migration with full schema"
```

---

## Task 3: DB helper (`bsebot/db.py`) — TDD

**Files:**
- Create: `/Users/devamarnani/Desktop/bsebot/tests/conftest.py`
- Create: `/Users/devamarnani/Desktop/bsebot/tests/test_db.py`
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/db.py`

- [ ] **Step 1: Write shared fixtures `tests/conftest.py`**

Write `/Users/devamarnani/Desktop/bsebot/tests/conftest.py`:

```python
import os
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "migrations"


@pytest.fixture
def tmp_db_path(tmp_path):
    return tmp_path / "bsebot_test.db"


@pytest.fixture
def migrations_dir():
    return MIGRATIONS_DIR
```

- [ ] **Step 2: Write failing tests `tests/test_db.py`**

Write `/Users/devamarnani/Desktop/bsebot/tests/test_db.py`:

```python
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
```

- [ ] **Step 3: Run tests — expect ImportError / failures**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_db.py -v`

Expected: ALL FAIL with `ModuleNotFoundError: No module named 'bsebot.db'` or attribute errors.

- [ ] **Step 4: Implement `bsebot/db.py`**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/db.py`:

```python
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
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_db.py -v`

Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
cd /Users/devamarnani/Desktop/bsebot
git add tests/conftest.py tests/test_db.py bsebot/db.py
git commit -m "feat(bsebot): add SQLite connection helper + migration runner with tests"
```

---

## Task 4: Config loader (`bsebot/config.py`) — TDD

**Files:**
- Create: `/Users/devamarnani/Desktop/bsebot/.env.example`
- Create: `/Users/devamarnani/Desktop/bsebot/config.yaml`
- Create: `/Users/devamarnani/Desktop/bsebot/tests/test_config.py`
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/config.py`

- [ ] **Step 1: Write `.env.example`**

Write `/Users/devamarnani/Desktop/bsebot/.env.example`:

```dotenv
# Required for primary provider (Gemini)
GEMINI_API_KEY=

# Required for fallback extractor (Cerebras)
CEREBRAS_API_KEY=

# Required for fallback reasoner (Groq)
GROQ_API_KEY=

# Required for weekly portfolio review (GitHub Models)
GITHUB_MODELS_TOKEN=
```

- [ ] **Step 2: Write `config.yaml`**

Write `/Users/devamarnani/Desktop/bsebot/config.yaml`:

```yaml
# BSEBot configuration.
# Models follow litellm naming. Env-var names below are looked up from .env.

database:
  path: data/bsebot.db

llm:
  extract_max_tokens: 2048
  reason_max_tokens: 8192
  continuation_max_attempts: 3
  providers:
    - name: gemini
      model: gemini/gemini-2.5-flash
      api_key_env: GEMINI_API_KEY
      roles: [extract, reason]
    - name: cerebras
      model: cerebras/llama-3.3-70b
      api_key_env: CEREBRAS_API_KEY
      roles: [extract]
    - name: groq
      model: groq/llama-3.3-70b-versatile
      api_key_env: GROQ_API_KEY
      roles: [reason]
    - name: github_models
      model: github/gpt-4.1
      api_key_env: GITHUB_MODELS_TOKEN
      roles: [reason]
```

- [ ] **Step 3: Write failing tests `tests/test_config.py`**

Write `/Users/devamarnani/Desktop/bsebot/tests/test_config.py`:

```python
import pytest

from bsebot import config as cfg


def _write(path, text):
    path.write_text(text, encoding="utf-8")


def test_load_reads_yaml_and_env(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    _write(
        cfg_path,
        "database:\n  path: data/bsebot.db\n"
        "llm:\n  extract_max_tokens: 2048\n  reason_max_tokens: 8192\n"
        "  continuation_max_attempts: 3\n"
        "  providers:\n"
        "    - name: gemini\n      model: gemini/gemini-2.5-flash\n"
        "      api_key_env: GEMINI_API_KEY\n      roles: [extract, reason]\n",
    )
    _write(env_path, "GEMINI_API_KEY=fake-key-123\n")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    loaded = cfg.load(cfg_path, env_path=env_path)

    assert loaded.database_path.endswith("data/bsebot.db")
    assert loaded.llm.extract_max_tokens == 2048
    assert loaded.llm.reason_max_tokens == 8192
    assert loaded.llm.continuation_max_attempts == 3
    assert len(loaded.llm.providers) == 1
    p = loaded.llm.providers[0]
    assert p.name == "gemini"
    assert p.api_key == "fake-key-123"
    assert "extract" in p.roles and "reason" in p.roles


def test_load_raises_when_required_env_missing(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"  # no file written
    _write(
        cfg_path,
        "database:\n  path: data/bsebot.db\n"
        "llm:\n  extract_max_tokens: 2048\n  reason_max_tokens: 8192\n"
        "  continuation_max_attempts: 3\n"
        "  providers:\n"
        "    - name: gemini\n      model: gemini/gemini-2.5-flash\n"
        "      api_key_env: GEMINI_API_KEY\n      roles: [extract]\n",
    )
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    with pytest.raises(cfg.ConfigError) as exc:
        cfg.load(cfg_path, env_path=env_path)
    assert "GEMINI_API_KEY" in str(exc.value)


def test_providers_for_role_filters(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    _write(
        cfg_path,
        "database:\n  path: data/bsebot.db\n"
        "llm:\n  extract_max_tokens: 2048\n  reason_max_tokens: 8192\n"
        "  continuation_max_attempts: 3\n"
        "  providers:\n"
        "    - name: gemini\n      model: gemini/gemini-2.5-flash\n"
        "      api_key_env: GEMINI_API_KEY\n      roles: [extract, reason]\n"
        "    - name: cerebras\n      model: cerebras/llama-3.3-70b\n"
        "      api_key_env: CEREBRAS_API_KEY\n      roles: [extract]\n",
    )
    _write(env_path, "GEMINI_API_KEY=g\nCEREBRAS_API_KEY=c\n")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)

    loaded = cfg.load(cfg_path, env_path=env_path)
    extract_chain = loaded.llm.providers_for_role("extract")
    reason_chain = loaded.llm.providers_for_role("reason")
    assert [p.name for p in extract_chain] == ["gemini", "cerebras"]
    assert [p.name for p in reason_chain] == ["gemini"]
```

- [ ] **Step 4: Run tests — expect FAIL**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_config.py -v`

Expected: `ModuleNotFoundError: No module named 'bsebot.config'`.

- [ ] **Step 5: Implement `bsebot/config.py`**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/config.py`:

```python
"""Load config.yaml + .env into typed objects."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml
from dotenv import load_dotenv


class ConfigError(RuntimeError):
    pass


@dataclass
class ProviderConfig:
    name: str
    model: str
    api_key_env: str
    api_key: str
    roles: list[str]


@dataclass
class LLMConfig:
    extract_max_tokens: int
    reason_max_tokens: int
    continuation_max_attempts: int
    providers: list[ProviderConfig] = field(default_factory=list)

    def providers_for_role(self, role: str) -> list[ProviderConfig]:
        return [p for p in self.providers if role in p.roles]


@dataclass
class AppConfig:
    database_path: str
    llm: LLMConfig


def load(config_path: str | Path, env_path: str | Path | None = None) -> AppConfig:
    """Load + validate config. Env vars are pulled from `env_path` (if given)
    then from process env. Missing required keys raise ConfigError."""
    config_path = Path(config_path)
    if env_path is not None:
        load_dotenv(dotenv_path=str(env_path), override=False)

    if not config_path.exists():
        raise ConfigError(f"config file not found: {config_path}")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    db_path = (raw.get("database") or {}).get("path")
    if not db_path:
        raise ConfigError("database.path missing in config.yaml")

    llm_raw = raw.get("llm") or {}
    try:
        extract_max = int(llm_raw["extract_max_tokens"])
        reason_max = int(llm_raw["reason_max_tokens"])
        cont_max = int(llm_raw["continuation_max_attempts"])
    except KeyError as e:
        raise ConfigError(f"llm.{e.args[0]} missing in config.yaml") from None

    providers_raw: Iterable[dict] = llm_raw.get("providers") or []
    providers: list[ProviderConfig] = []
    for entry in providers_raw:
        name = entry.get("name")
        model = entry.get("model")
        env_name = entry.get("api_key_env")
        roles = list(entry.get("roles") or [])
        if not (name and model and env_name and roles):
            raise ConfigError(
                f"provider entry missing required keys: {entry!r}"
            )
        api_key = os.environ.get(env_name, "")
        if not api_key:
            raise ConfigError(
                f"required env var {env_name} not set (needed by provider '{name}')"
            )
        providers.append(
            ProviderConfig(
                name=name,
                model=model,
                api_key_env=env_name,
                api_key=api_key,
                roles=roles,
            )
        )

    if not providers:
        raise ConfigError("at least one llm.providers entry is required")

    return AppConfig(
        database_path=db_path,
        llm=LLMConfig(
            extract_max_tokens=extract_max,
            reason_max_tokens=reason_max,
            continuation_max_attempts=cont_max,
            providers=providers,
        ),
    )
```

- [ ] **Step 6: Run tests — expect PASS**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_config.py -v`

Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
cd /Users/devamarnani/Desktop/bsebot
git add .env.example config.yaml tests/test_config.py bsebot/config.py
git commit -m "feat(bsebot): add config loader (YAML + .env) with role-filtered provider chain"
```

---

## Task 5: LLM router — provider fallback (TDD)

**Files:**
- Create: `/Users/devamarnani/Desktop/bsebot/tests/test_llm.py`
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/llm.py`

- [ ] **Step 1: Write failing test for provider fallback in `tests/test_llm.py`**

Write `/Users/devamarnani/Desktop/bsebot/tests/test_llm.py`:

```python
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from bsebot import db, llm
from bsebot.config import AppConfig, LLMConfig, ProviderConfig


class _Summary(BaseModel):
    summary: str


def _make_config(tmp_path):
    return AppConfig(
        database_path=str(tmp_path / "bsebot.db"),
        llm=LLMConfig(
            extract_max_tokens=2048,
            reason_max_tokens=8192,
            continuation_max_attempts=3,
            providers=[
                ProviderConfig("gemini", "gemini/gemini-2.5-flash",
                               "GEMINI_API_KEY", "k1", ["extract", "reason"]),
                ProviderConfig("cerebras", "cerebras/llama-3.3-70b",
                               "CEREBRAS_API_KEY", "k2", ["extract"]),
                ProviderConfig("groq", "groq/llama-3.3-70b-versatile",
                               "GROQ_API_KEY", "k3", ["reason"]),
            ],
        ),
    )


def _fake_completion(content: str, finish_reason: str = "stop",
                     input_tokens: int = 10, output_tokens: int = 5):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content),
            finish_reason=finish_reason,
        )],
        usage=SimpleNamespace(prompt_tokens=input_tokens,
                              completion_tokens=output_tokens),
    )


@pytest.fixture
def initialized_db(tmp_path, migrations_dir):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)
    return db_path


def test_reason_falls_back_on_rate_limit(monkeypatch, tmp_path, initialized_db):
    config = _make_config(tmp_path)
    config.database_path = str(initialized_db)
    router = llm.LLMRouter(config)

    calls = []

    def fake_completion(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"].startswith("gemini/"):
            raise llm.RateLimitError("429 rate limited")
        return _fake_completion("OK")

    monkeypatch.setattr(llm, "_completion", fake_completion)

    text, meta = router.reason([{"role": "user", "content": "ping"}])
    assert text == "OK"
    assert meta["provider"] == "groq"
    assert calls == ["gemini/gemini-2.5-flash", "groq/llama-3.3-70b-versatile"]


def test_extract_skips_providers_without_role(monkeypatch, tmp_path, initialized_db):
    config = _make_config(tmp_path)
    config.database_path = str(initialized_db)
    router = llm.LLMRouter(config)

    calls = []

    def fake_instructor_create(**kwargs):
        calls.append(kwargs["model"])
        return _Summary(summary="hello"), _fake_completion('{"summary":"hello"}')

    monkeypatch.setattr(llm, "_instructor_create", fake_instructor_create)

    obj, meta = router.extract("summarize", _Summary)
    assert isinstance(obj, _Summary)
    assert obj.summary == "hello"
    assert meta["provider"] == "gemini"
    # only providers with role=extract considered
    assert calls == ["gemini/gemini-2.5-flash"]


def test_logs_every_call_to_llm_call_log(monkeypatch, tmp_path, initialized_db):
    config = _make_config(tmp_path)
    config.database_path = str(initialized_db)
    router = llm.LLMRouter(config)

    monkeypatch.setattr(
        llm, "_completion",
        lambda **kw: _fake_completion("OK", input_tokens=7, output_tokens=2),
    )

    router.reason([{"role": "user", "content": "ping"}])

    conn = db.connect(initialized_db)
    try:
        rows = conn.execute(
            "SELECT provider, model, role, input_tokens, output_tokens, "
            "finish_reason, continuation_count FROM llm_call_log"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    provider, model, role, in_t, out_t, finish, cont = rows[0]
    assert provider == "gemini"
    assert model == "gemini/gemini-2.5-flash"
    assert role == "reason"
    assert in_t == 7
    assert out_t == 2
    assert finish == "stop"
    assert cont == 0


def test_continuation_on_truncated_response(monkeypatch, tmp_path, initialized_db):
    config = _make_config(tmp_path)
    config.database_path = str(initialized_db)
    router = llm.LLMRouter(config)

    responses = [
        _fake_completion("part one ", finish_reason="length"),
        _fake_completion("part two ", finish_reason="length"),
        _fake_completion("part three.", finish_reason="stop"),
    ]
    call_idx = {"i": 0}

    def fake_completion(**kwargs):
        i = call_idx["i"]
        call_idx["i"] += 1
        return responses[i]

    monkeypatch.setattr(llm, "_completion", fake_completion)

    text, meta = router.reason([{"role": "user", "content": "go"}])
    assert text == "part one part two part three."
    assert meta["provider"] == "gemini"
    assert meta["continuation_count"] == 2

    conn = db.connect(initialized_db)
    try:
        row = conn.execute(
            "SELECT continuation_count, finish_reason FROM llm_call_log"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == 2
    assert row[1] == "stop"


def test_continuation_exhausted_falls_through_to_next_provider(
    monkeypatch, tmp_path, initialized_db
):
    config = _make_config(tmp_path)
    config.database_path = str(initialized_db)
    router = llm.LLMRouter(config)

    def fake_completion(**kwargs):
        if kwargs["model"].startswith("gemini/"):
            return _fake_completion("partial", finish_reason="length")
        return _fake_completion("DONE", finish_reason="stop")

    monkeypatch.setattr(llm, "_completion", fake_completion)

    text, meta = router.reason([{"role": "user", "content": "go"}])
    assert text == "DONE"
    assert meta["provider"] == "groq"


def test_instructor_validates_schema(monkeypatch, tmp_path, initialized_db):
    config = _make_config(tmp_path)
    config.database_path = str(initialized_db)
    router = llm.LLMRouter(config)

    def fake_instructor_create(**kwargs):
        return _Summary(summary="hi"), _fake_completion('{"summary":"hi"}')

    monkeypatch.setattr(llm, "_instructor_create", fake_instructor_create)

    obj, _ = router.extract("p", _Summary)
    assert isinstance(obj, _Summary)
    assert obj.summary == "hi"
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_llm.py -v`

Expected: `ModuleNotFoundError: No module named 'bsebot.llm'`.

- [ ] **Step 3: Implement `bsebot/llm.py`**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/llm.py`:

```python
"""LLM router: provider chain fallback + truncation continuation + call logging.

Wraps litellm for raw completion calls, and instructor for structured (Pydantic) output.
External calls are routed through module-level shims (`_completion`, `_instructor_create`)
so tests can monkeypatch them without hitting the network.
"""

from __future__ import annotations

import time
from typing import Any, Type, TypeVar

from pydantic import BaseModel

from bsebot import db
from bsebot.config import AppConfig, ProviderConfig


T = TypeVar("T", bound=BaseModel)


# Continuation prompt per spec.
_CONTINUATION_PROMPT = (
    "Continue exactly where you left off. Do not repeat anything. No preamble."
)


# Per-provider quirks. Extendable as new providers are added.
PROVIDER_QUIRKS: dict[str, dict[str, Any]] = {
    "gemini": {"supports_system_message": True},
    "cerebras": {"supports_system_message": True, "max_context": 8192},
    "groq": {"supports_system_message": True},
    "github_models": {"supports_system_message": True, "rate_limit_per_week": 50},
}


# Rough USD cost per 1K tokens. Conservative defaults; refine later.
_COST_PER_1K = {
    "gemini": (0.00015, 0.0006),     # (input, output)
    "cerebras": (0.0, 0.0),          # free tier
    "groq": (0.0, 0.0),              # free tier
    "github_models": (0.0, 0.0),     # free tier (rate-limited)
}


class LLMError(RuntimeError):
    """Base class for router errors."""


class RateLimitError(LLMError):
    """Raised by underlying provider when rate-limited (HTTP 429 etc)."""


class APIError(LLMError):
    """Raised on transient API failure (5xx, network)."""


class AllProvidersFailedError(LLMError):
    """All providers in the chain raised an error."""


# ---------- shims so tests can patch them ----------

def _completion(**kwargs: Any) -> Any:
    """Thin wrapper over litellm.completion. Patched in tests."""
    import litellm  # local import keeps module importable without network

    try:
        return litellm.completion(**kwargs)
    except Exception as e:  # normalize provider errors
        msg = str(e).lower()
        if "rate" in msg or "429" in msg or "quota" in msg:
            raise RateLimitError(str(e)) from e
        raise APIError(str(e)) from e


def _instructor_create(**kwargs: Any) -> tuple[BaseModel, Any]:
    """Wrapper over instructor.from_litellm + completion. Patched in tests.

    Must return (parsed_model_instance, raw_response). The raw_response is
    used only for usage/finish_reason logging."""
    import instructor
    import litellm

    client = instructor.from_litellm(litellm.completion)
    response_model = kwargs.pop("response_model")
    raw = {}

    def _capture(**ck):
        out = _completion(**ck)
        raw["resp"] = out
        return out

    # instructor uses the same kwargs as litellm
    try:
        parsed = client.create(response_model=response_model, **kwargs)
    except Exception as e:
        msg = str(e).lower()
        if "rate" in msg or "429" in msg or "quota" in msg:
            raise RateLimitError(str(e)) from e
        raise APIError(str(e)) from e
    return parsed, raw.get("resp")


# ---------- core router ----------


class LLMRouter:
    def __init__(self, config: AppConfig) -> None:
        self.config = config

    # ----- public API -----

    def reason(
        self,
        messages: list[dict[str, str]],
        tools: list[dict] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Run a reasoning call. Returns (text, meta)."""
        chain = self.config.llm.providers_for_role("reason")
        if not chain:
            raise LLMError("no providers configured for role=reason")
        return self._run_chain(
            chain=chain,
            role="reason",
            messages=messages,
            tools=tools,
            max_tokens=self.config.llm.reason_max_tokens,
        )

    def extract(
        self,
        prompt: str,
        schema: Type[T],
    ) -> tuple[T, dict[str, Any]]:
        """Run a structured extraction. Returns (validated_model, meta)."""
        chain = self.config.llm.providers_for_role("extract")
        if not chain:
            raise LLMError("no providers configured for role=extract")
        last_err: Exception | None = None
        for provider in chain:
            t0 = time.time()
            try:
                parsed, raw = _instructor_create(
                    model=provider.model,
                    api_key=provider.api_key,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=self.config.llm.extract_max_tokens,
                    response_model=schema,
                )
            except (RateLimitError, APIError) as e:
                self._log(
                    provider=provider, role="extract",
                    in_t=0, out_t=0, finish=None,
                    duration_ms=int((time.time() - t0) * 1000),
                    continuation_count=0, error=str(e),
                )
                last_err = e
                continue
            in_t, out_t, finish = _usage(raw)
            self._log(
                provider=provider, role="extract",
                in_t=in_t, out_t=out_t, finish=finish,
                duration_ms=int((time.time() - t0) * 1000),
                continuation_count=0, error=None,
            )
            meta = {
                "provider": provider.name,
                "model": provider.model,
                "continuation_count": 0,
                "finish_reason": finish,
            }
            return parsed, meta
        raise AllProvidersFailedError(f"extract failed; last={last_err}")

    # ----- internals -----

    def _run_chain(
        self,
        chain: list[ProviderConfig],
        role: str,
        messages: list[dict[str, str]],
        tools: list[dict] | None,
        max_tokens: int,
    ) -> tuple[str, dict[str, Any]]:
        last_err: Exception | None = None
        for provider in chain:
            try:
                return self._call_with_continuation(
                    provider=provider, role=role,
                    messages=messages, tools=tools, max_tokens=max_tokens,
                )
            except (RateLimitError, APIError) as e:
                last_err = e
                continue
            except _TruncationGaveUp as e:
                last_err = e
                continue
        raise AllProvidersFailedError(f"{role} failed; last={last_err}")

    def _call_with_continuation(
        self,
        provider: ProviderConfig,
        role: str,
        messages: list[dict[str, str]],
        tools: list[dict] | None,
        max_tokens: int,
    ) -> tuple[str, dict[str, Any]]:
        running_messages = list(messages)
        accumulated = ""
        continuation_count = 0
        in_t_total = 0
        out_t_total = 0
        t0 = time.time()
        last_finish: str | None = None

        max_attempts = self.config.llm.continuation_max_attempts

        for attempt in range(max_attempts + 1):
            kwargs: dict[str, Any] = {
                "model": provider.model,
                "api_key": provider.api_key,
                "messages": running_messages,
                "max_tokens": max_tokens,
            }
            if tools:
                kwargs["tools"] = tools

            try:
                resp = _completion(**kwargs)
            except (RateLimitError, APIError) as e:
                self._log(
                    provider=provider, role=role,
                    in_t=in_t_total, out_t=out_t_total, finish=last_finish,
                    duration_ms=int((time.time() - t0) * 1000),
                    continuation_count=continuation_count, error=str(e),
                )
                raise

            in_t, out_t, finish = _usage(resp)
            in_t_total += in_t
            out_t_total += out_t
            last_finish = finish
            content = _content(resp) or ""
            accumulated += content

            if finish == "length" and attempt < max_attempts:
                continuation_count += 1
                running_messages = list(messages) + [
                    {"role": "assistant", "content": accumulated},
                    {"role": "user", "content": _CONTINUATION_PROMPT},
                ]
                continue

            if finish == "length":
                # exhausted continuations; log and bail to next provider
                self._log(
                    provider=provider, role=role,
                    in_t=in_t_total, out_t=out_t_total, finish=finish,
                    duration_ms=int((time.time() - t0) * 1000),
                    continuation_count=continuation_count,
                    error="continuation exhausted",
                )
                raise _TruncationGaveUp(provider.name)

            self._log(
                provider=provider, role=role,
                in_t=in_t_total, out_t=out_t_total, finish=finish,
                duration_ms=int((time.time() - t0) * 1000),
                continuation_count=continuation_count, error=None,
            )
            return accumulated, {
                "provider": provider.name,
                "model": provider.model,
                "continuation_count": continuation_count,
                "finish_reason": finish,
            }

        # safety net (should be unreachable)
        raise _TruncationGaveUp(provider.name)

    def _log(
        self,
        *,
        provider: ProviderConfig,
        role: str,
        in_t: int,
        out_t: int,
        finish: str | None,
        duration_ms: int,
        continuation_count: int,
        error: str | None,
    ) -> None:
        cost_in, cost_out = _COST_PER_1K.get(provider.name, (0.0, 0.0))
        cost = (in_t / 1000.0) * cost_in + (out_t / 1000.0) * cost_out
        conn = db.connect(self.config.database_path)
        try:
            conn.execute(
                "INSERT INTO llm_call_log "
                "(provider, model, role, input_tokens, output_tokens, "
                " finish_reason, duration_ms, continuation_count, "
                " cost_estimate_usd, error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    provider.name, provider.model, role,
                    int(in_t), int(out_t), finish, int(duration_ms),
                    int(continuation_count), float(cost), error,
                ),
            )
            conn.commit()
        finally:
            conn.close()


class _TruncationGaveUp(LLMError):
    def __init__(self, provider_name: str) -> None:
        super().__init__(f"truncation continuation exhausted for {provider_name}")


def _usage(resp: Any) -> tuple[int, int, str | None]:
    """Pull (input_tokens, output_tokens, finish_reason) out of a litellm response."""
    in_t = 0
    out_t = 0
    finish: str | None = None
    usage = getattr(resp, "usage", None)
    if usage is not None:
        in_t = int(getattr(usage, "prompt_tokens", 0) or 0)
        out_t = int(getattr(usage, "completion_tokens", 0) or 0)
    choices = getattr(resp, "choices", None) or []
    if choices:
        finish = getattr(choices[0], "finish_reason", None)
    return in_t, out_t, finish


def _content(resp: Any) -> str | None:
    choices = getattr(resp, "choices", None) or []
    if not choices:
        return None
    msg = getattr(choices[0], "message", None)
    if msg is None:
        return None
    return getattr(msg, "content", None)
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_llm.py -v`

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/devamarnani/Desktop/bsebot
git add tests/test_llm.py bsebot/llm.py
git commit -m "feat(bsebot): add LLM router with provider fallback, continuation, and call logging"
```

---

## Task 6: CLI scaffold (`bsebot/cli.py`) — TDD

**Files:**
- Create: `/Users/devamarnani/Desktop/bsebot/tests/test_cli.py`
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/cli.py`

- [ ] **Step 1: Write failing tests in `tests/test_cli.py`**

Write `/Users/devamarnani/Desktop/bsebot/tests/test_cli.py`:

```python
from pathlib import Path

import pytest
from click.testing import CliRunner

from bsebot import cli, db


REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_min_config(tmp_path, db_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "database:\n"
        f"  path: {db_path}\n"
        "llm:\n"
        "  extract_max_tokens: 2048\n"
        "  reason_max_tokens: 8192\n"
        "  continuation_max_attempts: 3\n"
        "  providers:\n"
        "    - name: gemini\n"
        "      model: gemini/gemini-2.5-flash\n"
        "      api_key_env: GEMINI_API_KEY\n"
        "      roles: [extract, reason]\n",
        encoding="utf-8",
    )
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=fake\n", encoding="utf-8")
    return cfg, env


def test_db_stats_lists_table_counts(tmp_path, migrations_dir):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)
    cfg, env = _write_min_config(tmp_path, db_path)

    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["--config", str(cfg), "--env", str(env), "db", "stats"],
    )
    assert result.exit_code == 0, result.output
    assert "raw_documents" in result.output
    assert "facts" in result.output


def test_db_query_rejects_non_select(tmp_path, migrations_dir):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)
    cfg, env = _write_min_config(tmp_path, db_path)

    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["--config", str(cfg), "--env", str(env),
         "db", "query", "DELETE FROM facts"],
    )
    assert result.exit_code != 0
    assert "read-only" in result.output.lower() or "select" in result.output.lower()


def test_db_query_accepts_select(tmp_path, migrations_dir):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)
    cfg, env = _write_min_config(tmp_path, db_path)

    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["--config", str(cfg), "--env", str(env),
         "db", "query", "SELECT name FROM sqlite_master WHERE type='table'"],
    )
    assert result.exit_code == 0, result.output
    assert "raw_documents" in result.output


def test_llm_test_pings_each_provider(monkeypatch, tmp_path, migrations_dir):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)
    cfg, env = _write_min_config(tmp_path, db_path)

    from bsebot import llm

    def fake_completion(**kwargs):
        from types import SimpleNamespace
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content="OK"),
                finish_reason="stop",
            )],
            usage=SimpleNamespace(prompt_tokens=4, completion_tokens=1),
        )

    monkeypatch.setattr(llm, "_completion", fake_completion)

    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["--config", str(cfg), "--env", str(env), "llm", "test"],
    )
    assert result.exit_code == 0, result.output
    assert "PASS" in result.output
    assert "gemini" in result.output


def test_stub_subcommand_prints_not_implemented(tmp_path, migrations_dir):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)
    cfg, env = _write_min_config(tmp_path, db_path)

    runner = CliRunner()
    result = runner.invoke(
        cli.main,
        ["--config", str(cfg), "--env", str(env), "harvest", "all"],
    )
    # stub: prints message and exits 0
    assert result.exit_code == 0
    assert "not yet implemented" in result.output.lower()
    assert "plan" in result.output.lower()
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_cli.py -v`

Expected: `ModuleNotFoundError: No module named 'bsebot.cli'`.

- [ ] **Step 3: Implement `bsebot/cli.py`**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/cli.py`:

```python
"""BSEBot CLI entry point. Foundation subcommands only:
`bsebot llm test`, `bsebot db stats`, `bsebot db query`.
All other subcommands stub out with a pointer to a future plan.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from bsebot import config as _config
from bsebot import db as _db
from bsebot import llm as _llm


_DEFAULT_CONFIG = "config.yaml"
_DEFAULT_ENV = ".env"


def _load_app_config(ctx: click.Context) -> _config.AppConfig:
    cfg_path = ctx.obj["config_path"]
    env_path = ctx.obj["env_path"]
    return _config.load(cfg_path, env_path=env_path)


@click.group()
@click.option(
    "--config", "config_path",
    default=_DEFAULT_CONFIG, show_default=True,
    help="Path to config.yaml",
)
@click.option(
    "--env", "env_path",
    default=_DEFAULT_ENV, show_default=True,
    help="Path to .env file with API keys",
)
@click.pass_context
def main(ctx: click.Context, config_path: str, env_path: str) -> None:
    """BSEBot — autonomous paper trading bot for BSE Ltd."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path
    ctx.obj["env_path"] = env_path


# --------- llm group ---------

@main.group()
def llm() -> None:
    """LLM router commands."""


@llm.command("test")
@click.pass_context
def llm_test(ctx: click.Context) -> None:
    """Send a trivial prompt to every configured provider and print PASS/FAIL."""
    cfg = _load_app_config(ctx)
    router = _llm.LLMRouter(cfg)
    prompt = "Reply with exactly 'OK' and nothing else."
    any_fail = False
    for provider in cfg.llm.providers:
        # Build a single-provider chain so we test each in isolation.
        single = _config.AppConfig(
            database_path=cfg.database_path,
            llm=_config.LLMConfig(
                extract_max_tokens=cfg.llm.extract_max_tokens,
                reason_max_tokens=cfg.llm.reason_max_tokens,
                continuation_max_attempts=cfg.llm.continuation_max_attempts,
                providers=[provider],
            ),
        )
        single_router = _llm.LLMRouter(single)
        try:
            if "reason" in provider.roles:
                text, _ = single_router.reason(
                    [{"role": "user", "content": prompt}]
                )
            else:
                # extract-only providers: send a tiny schema
                from pydantic import BaseModel

                class _Echo(BaseModel):
                    text: str

                obj, _ = single_router.extract(prompt, _Echo)
                text = obj.text
            click.echo(f"PASS  {provider.name}  ({provider.model})  -> {text!r}")
        except Exception as e:
            any_fail = True
            click.echo(f"FAIL  {provider.name}  ({provider.model})  -> {e}")
    if any_fail:
        sys.exit(1)


# --------- db group ---------

@main.group()
def db() -> None:
    """Database utilities."""


@db.command("stats")
@click.pass_context
def db_stats(ctx: click.Context) -> None:
    """Print row counts for every table."""
    cfg = _load_app_config(ctx)
    stats = _db.table_stats(cfg.database_path)
    width = max(len(name) for name in stats) if stats else 0
    for name in sorted(stats):
        click.echo(f"{name.ljust(width)}  {stats[name]}")


@db.command("query")
@click.argument("sql")
@click.pass_context
def db_query(ctx: click.Context, sql: str) -> None:
    """Run a read-only SELECT/WITH query and print rows as JSON lines."""
    if not _db.select_only(sql):
        click.echo(
            "ERROR: db query is read-only; only a single SELECT/WITH statement "
            "is allowed.", err=True,
        )
        sys.exit(2)
    cfg = _load_app_config(ctx)
    conn = _db.connect(cfg.database_path)
    try:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        for row in cur.fetchall():
            click.echo(json.dumps(dict(zip(cols, row)), default=str))
    finally:
        conn.close()


# --------- stubs for future plans ---------

_STUB_MSG = "not yet implemented — see future plan (plan 2+)"


def _stub(cmd_name: str):
    @click.pass_context
    def _impl(ctx: click.Context, **_kwargs) -> None:
        click.echo(f"{cmd_name}: {_STUB_MSG}")
    _impl.__name__ = f"stub_{cmd_name.replace(' ', '_')}"
    return _impl


@main.group()
def harvest() -> None:
    """Harvesters (stubbed in plan 1)."""


@harvest.command("all")
@click.pass_context
def harvest_all(ctx: click.Context) -> None:
    click.echo(f"harvest all: {_STUB_MSG}")


@harvest.command("one")
@click.argument("name")
@click.pass_context
def harvest_one(ctx: click.Context, name: str) -> None:
    click.echo(f"harvest {name}: {_STUB_MSG}")


@main.command("extract")
@click.pass_context
def extract_cmd(ctx: click.Context) -> None:
    """Run extractor (stubbed in plan 1)."""
    click.echo(f"extract: {_STUB_MSG}")


@main.group()
def agent() -> None:
    """Agent runner (stubbed in plan 1)."""


@agent.command("run")
@click.option("--triggered-by", default=None)
@click.pass_context
def agent_run(ctx: click.Context, triggered_by: str | None) -> None:
    click.echo(f"agent run: {_STUB_MSG}")


@main.group()
def positions() -> None:
    """Position manager (stubbed)."""


@positions.command("check")
@click.pass_context
def positions_check(ctx: click.Context) -> None:
    click.echo(f"positions check: {_STUB_MSG}")


@positions.command("list")
@click.pass_context
def positions_list(ctx: click.Context) -> None:
    click.echo(f"positions list: {_STUB_MSG}")


@main.group()
def alerts() -> None:
    """Alert system (stubbed)."""


@alerts.command("list")
@click.pass_context
def alerts_list(ctx: click.Context) -> None:
    click.echo(f"alerts list: {_STUB_MSG}")


@alerts.command("cancel")
@click.argument("alert_id", type=int)
@click.pass_context
def alerts_cancel(ctx: click.Context, alert_id: int) -> None:
    click.echo(f"alerts cancel {alert_id}: {_STUB_MSG}")


@main.group()
def vault() -> None:
    """Vault writer / Quartz (stubbed)."""


@vault.command("rebuild")
@click.pass_context
def vault_rebuild(ctx: click.Context) -> None:
    click.echo(f"vault rebuild: {_STUB_MSG}")


@vault.command("publish")
@click.pass_context
def vault_publish(ctx: click.Context) -> None:
    click.echo(f"vault publish: {_STUB_MSG}")


@vault.command("serve")
@click.pass_context
def vault_serve(ctx: click.Context) -> None:
    click.echo(f"vault serve: {_STUB_MSG}")


@main.group()
def report() -> None:
    """Reports (stubbed)."""


@report.command("daily")
@click.pass_context
def report_daily(ctx: click.Context) -> None:
    click.echo(f"report daily: {_STUB_MSG}")


@main.group()
def tools() -> None:
    """Tool registry (stubbed)."""


@tools.command("list")
@click.pass_context
def tools_list(ctx: click.Context) -> None:
    click.echo(f"tools list: {_STUB_MSG}")


@tools.command("approve")
@click.argument("tool_id", type=int)
@click.pass_context
def tools_approve(ctx: click.Context, tool_id: int) -> None:
    click.echo(f"tools approve {tool_id}: {_STUB_MSG}")


@main.command("trades")
@click.option("--days", default=None, type=int)
@click.pass_context
def trades_history(ctx: click.Context, days: int | None) -> None:
    """Trade history (stubbed)."""
    click.echo(f"trades history: {_STUB_MSG}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_cli.py -v`

Expected: 5 passed.

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest -v`

Expected: all tests pass (5 db + 3 config + 6 llm + 5 cli = 19 passed).

- [ ] **Step 6: Commit**

```bash
cd /Users/devamarnani/Desktop/bsebot
git add tests/test_cli.py bsebot/cli.py
git commit -m "feat(bsebot): add click CLI with db/llm subcommands and stubs for future plans"
```

---

## Task 7: `scripts/setup.sh` and `README.md`

**Files:**
- Create: `/Users/devamarnani/Desktop/bsebot/scripts/setup.sh`
- Create: `/Users/devamarnani/Desktop/bsebot/README.md`

- [ ] **Step 1: Write `scripts/setup.sh`**

Write `/Users/devamarnani/Desktop/bsebot/scripts/setup.sh`:

```bash
#!/usr/bin/env bash
# BSEBot setup. Idempotent: safe to re-run.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> Checking Python 3.11+"
if ! command -v python3.11 >/dev/null 2>&1; then
  echo "ERROR: python3.11 not found on PATH." >&2
  exit 1
fi
PY_VER=$(python3.11 -c 'import sys;print("%d.%d"%sys.version_info[:2])')
echo "    found python $PY_VER"

echo "==> Creating venv at .venv"
if [ ! -d ".venv" ]; then
  python3.11 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Installing dependencies (with dev extras)"
pip install --upgrade pip
pip install -e ".[dev]"

echo "==> Running migrations"
mkdir -p data logs
python3.11 -c "from pathlib import Path; from bsebot import db; \
db.run_migrations(Path('data/bsebot.db'), Path('migrations'))"

echo "==> Verifying .env"
if [ ! -f ".env" ]; then
  echo "ERROR: .env not found. Copy .env.example to .env and fill in keys." >&2
  exit 1
fi
MISSING=0
for key in GEMINI_API_KEY CEREBRAS_API_KEY GROQ_API_KEY GITHUB_MODELS_TOKEN; do
  if ! grep -E "^${key}=.+" .env >/dev/null; then
    echo "    missing: $key"
    MISSING=1
  fi
done
if [ "$MISSING" -ne 0 ]; then
  echo "ERROR: one or more required keys missing in .env" >&2
  exit 1
fi

echo "==> Pinging every configured provider (bsebot llm test)"
bsebot --config config.yaml --env .env llm test

echo "==> Setup complete."
```

- [ ] **Step 2: Make it executable**

Run: `cd /Users/devamarnani/Desktop/bsebot && chmod +x scripts/setup.sh`

Expected: no output, exit 0.

- [ ] **Step 3: Write `README.md`**

Write `/Users/devamarnani/Desktop/bsebot/README.md`:

```markdown
# BSEBot

Autonomous paper-trading bot for BSE Ltd. See
`docs/superpowers/specs/2026-05-17-bsebot-design.md` for the full design.

## Quick start

1. Install Python 3.11+.
2. Clone this repo. Production deploy lives at `/opt/bsebot/`; for local dev,
   work in your clone.
3. Copy keys:

   ```bash
   cp .env.example .env
   # edit .env, fill in GEMINI_API_KEY, CEREBRAS_API_KEY, GROQ_API_KEY,
   # GITHUB_MODELS_TOKEN
   ```

4. Run setup:

   ```bash
   ./scripts/setup.sh
   ```

   This creates `.venv/`, installs deps, runs migrations into `data/bsebot.db`,
   and pings every configured LLM provider with `bsebot llm test`.

5. Sanity checks:

   ```bash
   source .venv/bin/activate
   bsebot db stats
   bsebot db query "SELECT name FROM sqlite_master WHERE type='table'"
   bsebot llm test
   ```

## Swapping providers

Provider chain is defined in `config.yaml` under `llm.providers`. Order = priority.
To swap or reorder, edit the list. Each provider entry needs:

- `name` (free-form label, also used in logs)
- `model` (a [litellm](https://github.com/BerriAI/litellm) model id, e.g.
  `gemini/gemini-2.5-flash`, `groq/llama-3.3-70b-versatile`)
- `api_key_env` (env var that must be set in `.env`)
- `roles` (subset of `[extract, reason]`)

Re-run `bsebot llm test` after changes.

## Free API keys

- **Gemini** (Google AI Studio): https://aistudio.google.com/apikey — 1500 req/day free
- **Cerebras**: https://cloud.cerebras.ai/ — 1M tokens/day free
- **Groq**: https://console.groq.com/ — 14k req/day free
- **GitHub Models**: https://github.com/marketplace/models — PAT with
  `models:read` scope

## Plan 1 scope

This commit implements only the foundation: SQLite schema, config loader, LLM
router, and the `db` / `llm` CLI groups. All other subcommands print
`not yet implemented` until later plans land.
```

- [ ] **Step 4: Commit**

```bash
cd /Users/devamarnani/Desktop/bsebot
git add scripts/setup.sh README.md
git commit -m "feat(bsebot): add setup.sh and README for foundation"
```

---

## Self-Review Notes (post-write)

**Spec coverage of Plan 1 scope:**
- Project skeleton at `/opt/bsebot/` layout — covered (Task 1, 7). Local dev under `/Users/devamarnani/Desktop/bsebot/`; setup.sh deploys to `data/`, `.venv/`, `logs/`.
- `pyproject.toml` with all spec deps — covered (Task 1). Includes litellm, instructor, pydantic, dotenv, pyyaml, click, requests, httpx, bs4, lxml, pdfplumber, feedparser, yfinance, matplotlib, frontmatter, apscheduler.
- `bsebot/db.py` (WAL, FK, migration runner) — Task 3.
- `bsebot/config.py` (yaml + dotenv, raises on missing) — Task 4.
- `migrations/001_initial.sql` for all 11 spec tables — Task 2.
- `config.yaml` with 4-provider chain — Task 4. (Roles assigned per spec: Gemini does both; Cerebras extract-only; Groq + GitHub Models reason.)
- `.env.example` with 4 keys — Task 4.
- `scripts/setup.sh` (Python check, venv, install, migrations, .env verify, `bsebot llm test`) — Task 7.
- `.gitignore` — Task 1.
- `README.md` minimal — Task 7.
- `bsebot/llm.py` with `extract`/`reason`, litellm + instructor, fallback chain, truncation continuation (up to 3 attempts), PROVIDER_QUIRKS, `max_tokens` explicit, llm_call_log writes — Task 5.
- `bsebot/cli.py` with `bsebot llm test`, `bsebot db stats`, `bsebot db query` + stubs — Task 6.
- All tests mock external APIs via `monkeypatch.setattr(llm, "_completion", ...)` and `_instructor_create` — no network in CI. The only real-API call is the manual `bsebot llm test` from setup.sh.

**Placeholder scan:** Searched plan for "TBD", "TODO", "implement later", "similar to". None present. Every code step shows full code.

**Type/name consistency:**
- `LLMRouter.extract()` / `LLMRouter.reason()` — used consistently in llm.py, tests, and CLI.
- `db.run_migrations(db_path, migrations_dir)` — same signature in `bsebot/db.py`, conftest fixture, and tests.
- `db.table_stats(db_path)` — same in module + CLI.
- `db.select_only(sql)` — same in module + CLI.
- `config.load(config_path, env_path=...)` — same in tests + CLI.
- `AppConfig.database_path`, `AppConfig.llm.providers_for_role()` — used consistently.
- Module-level shims `_completion` and `_instructor_create` are referenced in both implementation and tests with matching names.

**Items intentionally deferred to Plan 2+:**
- Harvesters (base class + sebi_circulars + others) — build-order step 3 and step 13.
- Extractor with Pydantic schemas, instructor, quote verification, deterministic numeric parsers — step 4.
- Vault writer + Quartz integration — steps 6, 12.
- Agent runner + alert system + price watcher + position manager + ₹10/day overhead + adversarial fact-checker — steps 7–11.
- Reporter + P&L chart generation — step 11.
- Bootstrap CLI command (`bsebot bootstrap`).
- systemd units, cron suggestions, cloudflared snippet — setup.sh currently stops at `bsebot llm test`; deployment-side install is for a later plan.
- `agent_memory` supersedence triggers and `alerts` auto-deactivate triggers (DDL has the FK columns but no SQL triggers yet — those go in the migration that introduces the agent runner).
