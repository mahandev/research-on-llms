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
