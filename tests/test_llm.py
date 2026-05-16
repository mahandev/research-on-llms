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
