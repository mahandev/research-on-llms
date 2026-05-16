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
