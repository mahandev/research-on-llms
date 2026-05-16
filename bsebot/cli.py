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
