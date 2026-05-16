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
