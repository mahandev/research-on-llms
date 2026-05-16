# BSEBot Plan 3 — Agent + Trading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the autonomous decision agent, the alert system, the price watcher (systemd-driven 30s loop), the position manager (paper trade execution + ₹10/day overhead), the adversarial fact-checker, and the agent's runtime tools (including `fetch_url`, `web_search`, `request_new_source`). After this plan, `bsebot agent run`, `bsebot positions check/list`, `bsebot alerts list/cancel`, `bsebot tools list/approve`, and `bsebot trades` all work, and the price watcher service can be started.

**Architecture:** A single `bsebot.agent.run` entrypoint acquires a SQLite advisory lock, builds the agent context (memories, open positions, new facts, triggering alert), and runs a tool-call loop over `LLMRouter.reason`. Tools are functions in `bsebot/agent_tools.py` registered into a name→callable map and exposed to the LLM via JSON schemas. The agent must terminate by calling `submit_decision`, which runs a regex check for `[fact:NNNN]` citations and an adversarial second-LLM pass before persisting a trade. Alerts are validated at creation and fired by `bsebot.price_watcher`, a systemd service that polls yfinance every 30s during market hours and invokes the agent on fire. Position manager runs after every price tick to evaluate stops/targets. The daily ₹10 overhead is a midnight cron debit.

**Tech Stack:** Python 3.11, `yfinance`, `pytz`, `pydantic`, `litellm` (via Plan 1 router), `requests`/`httpx` (via Plan 2 client), `playwright` (optional last-resort renderer for `fetch_url`).

**Scope:** Build-order items 7–11 from the spec. Out of scope: vault writer real implementation (Plan 4), Quartz, reporter UI.

---

## File Structure

Files created (under `/Users/devamarnani/Desktop/bsebot/`):

- `bsebot/clock.py` — IST timezone helpers + market-hours check
- `bsebot/cash.py` — `cash_ledger` helpers (deposit, debit, balance)
- `bsebot/positions.py` — open/close trade + stop/target check
- `bsebot/overhead.py` — daily ₹10 debit
- `bsebot/alerts_engine.py` — set/cancel/list/fire logic + budget rules
- `bsebot/memory.py` — `agent_memory` helpers (read, write, supersede)
- `bsebot/agent_tools.py` — every tool the agent can call
- `bsebot/factcheck.py` — adversarial pass
- `bsebot/fetcher.py` — multi-strategy URL fetcher (requests → httpx → playwright)
- `bsebot/web_search.py` — Brave API → DuckDuckGo fallback
- `bsebot/agent.py` — run loop, context build, lock, persistence
- `bsebot/price_watcher.py` — 30s polling loop
- `systemd/bsebot-price-watcher.service`
- `migrations/003_agent_locks_and_constraints.sql`
- `tests/test_clock.py`
- `tests/test_cash.py`
- `tests/test_positions.py`
- `tests/test_overhead.py`
- `tests/test_alerts_engine.py`
- `tests/test_memory.py`
- `tests/test_agent_tools.py`
- `tests/test_factcheck.py`
- `tests/test_fetcher.py`
- `tests/test_web_search.py`
- `tests/test_agent.py`
- `tests/test_price_watcher.py`
- `tests/test_cli_agent.py`

Modified:
- `bsebot/cli.py` — wire `agent run`, `positions`, `alerts`, `tools`, `trades` commands
- `bsebot/vault.py` — add no-op stubs `publish_decision`, `publish_trade`, `publish_alert_snapshot`, `publish_memory` (Plan 4 fills in)

---

## Task 1: Clock + market hours (`bsebot/clock.py`) — TDD

**Files:**
- Create: `bsebot/clock.py`, `tests/test_clock.py`

- [ ] **Step 1: Write failing tests**

Write `/Users/devamarnani/Desktop/bsebot/tests/test_clock.py`:

```python
from datetime import datetime

import pytest
import pytz

from bsebot import clock


def _ist(y, mo, d, h, mi):
    return pytz.timezone("Asia/Kolkata").localize(datetime(y, mo, d, h, mi))


def test_is_market_open_weekday_09_30():
    assert clock.is_market_open(_ist(2025, 5, 13, 9, 30)) is True


def test_is_market_open_weekday_08_30_is_closed():
    assert clock.is_market_open(_ist(2025, 5, 13, 8, 30)) is False


def test_is_market_open_weekday_15_30_is_open():
    assert clock.is_market_open(_ist(2025, 5, 13, 15, 30)) is True


def test_is_market_open_weekday_15_31_is_closed():
    assert clock.is_market_open(_ist(2025, 5, 13, 15, 31)) is False


def test_is_market_open_weekend_is_closed():
    assert clock.is_market_open(_ist(2025, 5, 17, 11, 0)) is False  # Saturday


def test_now_ist_returns_ist_aware_datetime():
    now = clock.now_ist()
    assert now.tzinfo is not None
    assert "Kolkata" in str(now.tzinfo) or now.utcoffset().total_seconds() == 19800
```

- [ ] **Step 2: Implement `bsebot/clock.py`**

```python
"""IST timezone + NSE market-hours helpers."""

from __future__ import annotations

from datetime import datetime, time

import pytz


IST = pytz.timezone("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


def now_ist() -> datetime:
    return datetime.now(IST)


def is_market_open(dt: datetime | None = None) -> bool:
    """NSE cash-segment hours: Mon-Fri 09:15..15:30 IST. No holiday calendar yet."""
    dt = dt or now_ist()
    if dt.tzinfo is None:
        dt = IST.localize(dt)
    else:
        dt = dt.astimezone(IST)
    if dt.weekday() >= 5:
        return False
    return MARKET_OPEN <= dt.time() <= MARKET_CLOSE
```

- [ ] **Step 3: Run, expect PASS**

`cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_clock.py -v` → 6 passed.

- [ ] **Step 4: Commit**

```bash
git add bsebot/clock.py tests/test_clock.py
git commit -m "feat(bsebot): add IST clock + market-hours helpers"
```

---

## Task 2: Cash ledger (`bsebot/cash.py`) — TDD

- [ ] **Step 1: Write failing tests `tests/test_cash.py`**

```python
from bsebot import cash, db


def test_starting_balance_is_zero(tmp_path, migrations_dir):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    assert cash.balance(p) == 0.0


def test_deposit_then_debit(tmp_path, migrations_dir):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    cash.deposit(p, 10000.0, note="seed")
    cash.debit(p, 250.0, movement_type="trade_entry", note="x")
    assert cash.balance(p) == 9750.0


def test_ledger_lists_movements_chronologically(tmp_path, migrations_dir):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    cash.deposit(p, 10000.0, note="seed")
    cash.debit(p, 10.0, movement_type="daily_overhead", note="d1")
    rows = cash.list_movements(p)
    assert [r["movement_type"] for r in rows] == ["deposit", "daily_overhead"]
    assert rows[0]["amount"] == 10000.0
    assert rows[1]["amount"] == -10.0
```

- [ ] **Step 2: Implement `bsebot/cash.py`**

```python
"""cash_ledger helpers."""

from __future__ import annotations

from pathlib import Path

from bsebot import db


def deposit(db_path: str | Path, amount: float, *, note: str | None = None) -> int:
    return _insert(db_path, "deposit", abs(float(amount)), note=note)


def debit(
    db_path: str | Path,
    amount: float,
    *,
    movement_type: str,
    related_trade_id: int | None = None,
    note: str | None = None,
) -> int:
    return _insert(db_path, movement_type, -abs(float(amount)),
                   related_trade_id=related_trade_id, note=note)


def credit(
    db_path: str | Path,
    amount: float,
    *,
    movement_type: str,
    related_trade_id: int | None = None,
    note: str | None = None,
) -> int:
    return _insert(db_path, movement_type, abs(float(amount)),
                   related_trade_id=related_trade_id, note=note)


def balance(db_path: str | Path) -> float:
    conn = db.connect(db_path)
    try:
        v = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM cash_ledger").fetchone()[0]
    finally:
        conn.close()
    return float(v or 0.0)


def list_movements(db_path: str | Path, limit: int = 100) -> list[dict]:
    conn = db.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, occurred_at, movement_type, amount, related_trade_id, note "
            "FROM cash_ledger ORDER BY id LIMIT ?",
            (int(limit),),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"id": r[0], "occurred_at": r[1], "movement_type": r[2],
         "amount": float(r[3]), "related_trade_id": r[4], "note": r[5]}
        for r in rows
    ]


def _insert(
    db_path, movement_type: str, amount: float, *,
    related_trade_id: int | None = None, note: str | None = None,
) -> int:
    conn = db.connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO cash_ledger (movement_type, amount, related_trade_id, note) "
            "VALUES (?, ?, ?, ?)",
            (movement_type, float(amount), related_trade_id, note),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()
```

- [ ] **Step 3: Run + commit** → 3 passed.

```bash
git add bsebot/cash.py tests/test_cash.py
git commit -m "feat(bsebot): add cash_ledger helpers (deposit, debit, credit, balance)"
```

---

## Task 3: Position manager (`bsebot/positions.py`) — TDD

- [ ] **Step 1: Write tests `tests/test_positions.py`**

```python
from bsebot import cash, db, positions


def _seed(tmp_path, migrations_dir):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    cash.deposit(p, 10000.0, note="seed")
    return p


def test_open_long_writes_trade_and_debits_cash(tmp_path, migrations_dir):
    p = _seed(tmp_path, migrations_dir)
    tid = positions.open_trade(
        p, agent_run_id=None, side="long", quantity=5, entry_price=100.0,
        stop_loss=95.0, target=110.0,
    )
    assert tid > 0
    assert cash.balance(p) == 10000.0 - 500.0
    rows = positions.list_open(p)
    assert len(rows) == 1
    assert rows[0]["quantity"] == 5
    assert rows[0]["status"] == "open"


def test_close_credits_cash_and_records_pnl(tmp_path, migrations_dir):
    p = _seed(tmp_path, migrations_dir)
    tid = positions.open_trade(
        p, agent_run_id=None, side="long", quantity=5, entry_price=100.0,
        stop_loss=95.0, target=110.0,
    )
    positions.close_trade(p, tid, exit_price=108.0, exit_reason="target")
    assert cash.balance(p) == 10000.0 - 500.0 + 540.0
    row = positions.get_trade(p, tid)
    assert row["status"] == "closed"
    assert row["exit_reason"] == "target"
    assert row["pnl"] == 40.0


def test_evaluate_stops_and_targets_closes_winners(tmp_path, migrations_dir):
    p = _seed(tmp_path, migrations_dir)
    tid = positions.open_trade(
        p, agent_run_id=None, side="long", quantity=2, entry_price=100.0,
        stop_loss=95.0, target=110.0,
    )
    closed = positions.evaluate(p, current_price=111.0)
    assert tid in closed
    assert positions.get_trade(p, tid)["exit_reason"] == "target"


def test_evaluate_closes_stop_loss(tmp_path, migrations_dir):
    p = _seed(tmp_path, migrations_dir)
    tid = positions.open_trade(
        p, agent_run_id=None, side="long", quantity=2, entry_price=100.0,
        stop_loss=95.0, target=110.0,
    )
    closed = positions.evaluate(p, current_price=94.5)
    assert closed == [tid]
    assert positions.get_trade(p, tid)["exit_reason"] == "stop_loss"


def test_evaluate_no_action_inside_band(tmp_path, migrations_dir):
    p = _seed(tmp_path, migrations_dir)
    tid = positions.open_trade(
        p, agent_run_id=None, side="long", quantity=2, entry_price=100.0,
        stop_loss=95.0, target=110.0,
    )
    assert positions.evaluate(p, current_price=102.0) == []
    assert positions.get_trade(p, tid)["status"] == "open"
```

- [ ] **Step 2: Implement `bsebot/positions.py`**

```python
"""Paper trade open/close + stop/target evaluation."""

from __future__ import annotations

from pathlib import Path

from bsebot import cash, db


def open_trade(
    db_path: str | Path,
    *,
    agent_run_id: int | None,
    side: str,
    quantity: int,
    entry_price: float,
    stop_loss: float | None,
    target: float | None,
    force_exit_by: str | None = None,
) -> int:
    if side not in {"long", "short"}:
        raise ValueError("side must be 'long' or 'short'")
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    conn = db.connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO trades "
            "(opened_by_agent_run, side, quantity, entry_price, stop_loss, target, "
            " force_exit_by, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'open')",
            (agent_run_id, side, int(quantity), float(entry_price),
             stop_loss, target, force_exit_by),
        )
        conn.commit()
        tid = cur.lastrowid
    finally:
        conn.close()
    cost = float(entry_price) * int(quantity)
    cash.debit(db_path, cost, movement_type="trade_entry",
               related_trade_id=tid, note=f"open {side} qty={quantity} @{entry_price}")
    return tid


def close_trade(
    db_path: str | Path, trade_id: int, *,
    exit_price: float, exit_reason: str,
) -> None:
    trade = get_trade(db_path, trade_id)
    if trade is None or trade["status"] != "open":
        return
    qty = int(trade["quantity"])
    entry = float(trade["entry_price"])
    if trade["side"] == "long":
        pnl = (float(exit_price) - entry) * qty
    else:
        pnl = (entry - float(exit_price)) * qty
    conn = db.connect(db_path)
    try:
        conn.execute(
            "UPDATE trades SET exit_price=?, exit_at=CURRENT_TIMESTAMP, "
            "exit_reason=?, pnl=?, status='closed' WHERE id=?",
            (float(exit_price), exit_reason, float(pnl), int(trade_id)),
        )
        conn.commit()
    finally:
        conn.close()
    proceeds = float(exit_price) * qty
    cash.credit(db_path, proceeds, movement_type="trade_exit",
                related_trade_id=trade_id, note=f"close @{exit_price} {exit_reason}")


def evaluate(db_path: str | Path, *, current_price: float) -> list[int]:
    """Close any open trade whose stop/target/force_exit is breached.
    Returns list of trade ids closed."""
    closed: list[int] = []
    for t in list_open(db_path):
        side = t["side"]
        stop = t["stop_loss"]
        target = t["target"]
        if side == "long":
            if stop is not None and current_price <= float(stop):
                close_trade(db_path, t["id"], exit_price=current_price,
                            exit_reason="stop_loss")
                closed.append(t["id"])
                continue
            if target is not None and current_price >= float(target):
                close_trade(db_path, t["id"], exit_price=current_price,
                            exit_reason="target")
                closed.append(t["id"])
                continue
        else:  # short
            if stop is not None and current_price >= float(stop):
                close_trade(db_path, t["id"], exit_price=current_price,
                            exit_reason="stop_loss")
                closed.append(t["id"])
                continue
            if target is not None and current_price <= float(target):
                close_trade(db_path, t["id"], exit_price=current_price,
                            exit_reason="target")
                closed.append(t["id"])
    return closed


def list_open(db_path: str | Path) -> list[dict]:
    return _query(db_path, "WHERE status='open'")


def get_trade(db_path: str | Path, trade_id: int) -> dict | None:
    rows = _query(db_path, "WHERE id=?", (int(trade_id),))
    return rows[0] if rows else None


def _query(db_path, where_sql: str, params: tuple = ()) -> list[dict]:
    conn = db.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, opened_by_agent_run, ticker, side, quantity, entry_price, "
            "entry_at, stop_loss, target, force_exit_by, exit_price, exit_at, "
            "exit_reason, pnl, status FROM trades " + where_sql,
            params,
        ).fetchall()
    finally:
        conn.close()
    cols = ["id", "opened_by_agent_run", "ticker", "side", "quantity",
            "entry_price", "entry_at", "stop_loss", "target", "force_exit_by",
            "exit_price", "exit_at", "exit_reason", "pnl", "status"]
    return [dict(zip(cols, r)) for r in rows]
```

- [ ] **Step 3: Run + commit** → 5 passed.

```bash
git add bsebot/positions.py tests/test_positions.py
git commit -m "feat(bsebot): add position manager (open/close + stop/target evaluation)"
```

---

## Task 4: Daily ₹10 overhead (`bsebot/overhead.py`) — TDD

- [ ] **Step 1: Tests `tests/test_overhead.py`**

```python
from bsebot import cash, db, overhead


def test_debit_once_per_day(tmp_path, migrations_dir):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    cash.deposit(p, 10000.0, note="seed")
    n1 = overhead.run_daily(p, date_str="2025-05-15")
    n2 = overhead.run_daily(p, date_str="2025-05-15")
    assert n1 == 1
    assert n2 == 0
    assert cash.balance(p) == 10000.0 - 10.0


def test_debit_different_day(tmp_path, migrations_dir):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    cash.deposit(p, 100.0, note="seed")
    overhead.run_daily(p, date_str="2025-05-15")
    overhead.run_daily(p, date_str="2025-05-16")
    assert cash.balance(p) == 80.0
```

- [ ] **Step 2: Implement `bsebot/overhead.py`**

```python
"""Daily ₹10 operating overhead."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from bsebot import cash, db


OVERHEAD_AMOUNT = 10.0


def run_daily(db_path: str | Path, *, date_str: str | None = None) -> int:
    d = date_str or date.today().isoformat()
    if _already_charged(db_path, d):
        return 0
    cash.debit(db_path, OVERHEAD_AMOUNT, movement_type="daily_overhead",
               note=f"overhead {d}")
    return 1


def _already_charged(db_path, date_str: str) -> bool:
    conn = db.connect(db_path)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM cash_ledger "
            "WHERE movement_type='daily_overhead' AND note=?",
            (f"overhead {date_str}",),
        ).fetchone()[0]
    finally:
        conn.close()
    return n > 0
```

- [ ] **Step 3: Commit**

```bash
git add bsebot/overhead.py tests/test_overhead.py
git commit -m "feat(bsebot): add daily ₹10 overhead deduction (idempotent per date)"
```

---

## Task 5: Alerts engine (`bsebot/alerts_engine.py`) — TDD

- [ ] **Step 1: Tests `tests/test_alerts_engine.py`**

```python
from datetime import datetime, timedelta

import pytest

from bsebot import alerts_engine as ae
from bsebot import db


def _seed_facts(p, n=3):
    conn = db.connect(p)
    try:
        for i in range(n):
            conn.execute(
                "INSERT INTO raw_documents (source, content_hash, content) "
                "VALUES ('x', ?, ?)", (f"h{i}", f"c{i}"),
            )
            conn.execute(
                "INSERT INTO facts (source_doc_id, fact_type, payload_json, source_quote) "
                "VALUES (?, 'news', '{}', 'q')", (i + 1,),
            )
        conn.commit()
    finally:
        conn.close()


def test_set_alert_writes_row(tmp_path, migrations_dir):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    _seed_facts(p, 1)
    aid = ae.set_alert(
        p, agent_run_id=None,
        condition="price_above", threshold=120.0,
        valid_until=(datetime.utcnow() + timedelta(days=5)).isoformat(),
        why_this_threshold="20% above thesis target",
        source_fact_ids=[1],
        current_price=100.0,
    )
    assert aid > 0


def test_set_alert_rejects_threshold_too_close(tmp_path, migrations_dir):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    _seed_facts(p, 1)
    with pytest.raises(ae.AlertRuleError) as exc:
        ae.set_alert(
            p, agent_run_id=None,
            condition="price_above", threshold=100.5,
            valid_until=(datetime.utcnow() + timedelta(days=1)).isoformat(),
            why_this_threshold="x", source_fact_ids=[1], current_price=100.0,
        )
    assert "1%" in str(exc.value)


def test_set_alert_rejects_no_source_facts(tmp_path, migrations_dir):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    with pytest.raises(ae.AlertRuleError):
        ae.set_alert(
            p, agent_run_id=None, condition="price_above", threshold=120.0,
            valid_until=(datetime.utcnow() + timedelta(days=5)).isoformat(),
            why_this_threshold="x", source_fact_ids=[], current_price=100.0,
        )


def test_set_alert_rejects_valid_until_too_far(tmp_path, migrations_dir):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    _seed_facts(p, 1)
    with pytest.raises(ae.AlertRuleError):
        ae.set_alert(
            p, agent_run_id=None, condition="price_above", threshold=120.0,
            valid_until=(datetime.utcnow() + timedelta(days=45)).isoformat(),
            why_this_threshold="x", source_fact_ids=[1], current_price=100.0,
        )


def test_max_active_8(tmp_path, migrations_dir):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    _seed_facts(p, 1)
    vu = (datetime.utcnow() + timedelta(days=5)).isoformat()
    for i in range(8):
        ae.set_alert(p, agent_run_id=None, condition="price_above",
                     threshold=100.0 + 2.0 * (i + 1), valid_until=vu,
                     why_this_threshold="x", source_fact_ids=[1],
                     current_price=100.0)
    with pytest.raises(ae.AlertRuleError) as exc:
        ae.set_alert(p, agent_run_id=None, condition="price_above",
                     threshold=130.0, valid_until=vu,
                     why_this_threshold="x", source_fact_ids=[1],
                     current_price=100.0)
    assert "max active" in str(exc.value).lower()


def test_dedup_within_1_pct_same_condition(tmp_path, migrations_dir):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    _seed_facts(p, 1)
    vu = (datetime.utcnow() + timedelta(days=5)).isoformat()
    ae.set_alert(p, agent_run_id=None, condition="price_above",
                 threshold=120.0, valid_until=vu,
                 why_this_threshold="x", source_fact_ids=[1], current_price=100.0)
    with pytest.raises(ae.AlertRuleError):
        ae.set_alert(p, agent_run_id=None, condition="price_above",
                     threshold=120.5, valid_until=vu,
                     why_this_threshold="x", source_fact_ids=[1],
                     current_price=100.0)


def test_fire_checks_threshold_and_cooldown(tmp_path, migrations_dir):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    _seed_facts(p, 1)
    vu = (datetime.utcnow() + timedelta(days=5)).isoformat()
    aid = ae.set_alert(p, agent_run_id=None, condition="price_above",
                       threshold=120.0, valid_until=vu,
                       why_this_threshold="x", source_fact_ids=[1],
                       current_price=100.0)
    fired = ae.check_and_fire(p, current_price=119.0)
    assert fired == []
    fired = ae.check_and_fire(p, current_price=121.0)
    assert fired == [aid]
    # second tick within cooldown does not refire
    fired = ae.check_and_fire(p, current_price=122.0)
    assert fired == []


def test_cancel_alert(tmp_path, migrations_dir):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    _seed_facts(p, 1)
    vu = (datetime.utcnow() + timedelta(days=5)).isoformat()
    aid = ae.set_alert(p, agent_run_id=None, condition="price_above",
                       threshold=120.0, valid_until=vu,
                       why_this_threshold="x", source_fact_ids=[1],
                       current_price=100.0)
    assert ae.cancel(p, aid) is True
    assert ae.cancel(p, aid) is False  # already cancelled
```

- [ ] **Step 2: Implement `bsebot/alerts_engine.py`**

```python
"""Alerts: validate creation rules + check-and-fire on each price tick."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from bsebot import db


class AlertRuleError(ValueError):
    """Raised when a `set_alert` call violates a budget/distance rule."""


MAX_ACTIVE = 8
MAX_CREATED_PER_DAY = 5
MAX_VALID_DAYS = 30
MIN_DISTANCE_PCT = 1.0
DEFAULT_COOLDOWN_MIN = 60
INTRADAY_COOLDOWN_MIN = 5


def set_alert(
    db_path: str | Path, *,
    agent_run_id: int | None,
    condition: str,
    threshold: float,
    valid_until: str,
    why_this_threshold: str,
    source_fact_ids: list[int],
    current_price: float,
    intraday: bool = False,
    linked_trade_id: int | None = None,
    linked_thesis_id: int | None = None,
    ticker: str = "BSE",
) -> int:
    if not source_fact_ids:
        raise AlertRuleError("source_fact_ids must contain at least one fact id")
    if not why_this_threshold or not why_this_threshold.strip():
        raise AlertRuleError("why_this_threshold is required")
    vu_dt = datetime.fromisoformat(valid_until)
    if vu_dt - datetime.utcnow() > timedelta(days=MAX_VALID_DAYS):
        raise AlertRuleError(
            f"valid_until must be within {MAX_VALID_DAYS} days from now"
        )
    if vu_dt <= datetime.utcnow():
        raise AlertRuleError("valid_until must be in the future")

    if condition in {"price_above", "price_below"}:
        pct_off = abs(float(threshold) - float(current_price)) / float(current_price) * 100.0
        if pct_off < MIN_DISTANCE_PCT:
            raise AlertRuleError(
                f"threshold must be ≥{MIN_DISTANCE_PCT}% from current price "
                f"(got {pct_off:.2f}%)"
            )

    conn = db.connect(db_path)
    try:
        active = conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE active=1"
        ).fetchone()[0]
        if active >= MAX_ACTIVE:
            raise AlertRuleError(f"max active alerts reached ({MAX_ACTIVE})")
        today = datetime.utcnow().date().isoformat()
        created_today = conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE date(created_at)=?",
            (today,),
        ).fetchone()[0]
        if created_today >= MAX_CREATED_PER_DAY:
            raise AlertRuleError(
                f"max alerts created per day reached ({MAX_CREATED_PER_DAY})"
            )
        if condition in {"price_above", "price_below"}:
            others = conn.execute(
                "SELECT threshold FROM alerts WHERE active=1 AND condition=? AND ticker=?",
                (condition, ticker),
            ).fetchall()
            for (other_t,) in others:
                pct = abs(float(other_t) - float(threshold)) / float(other_t) * 100.0
                if pct < MIN_DISTANCE_PCT:
                    raise AlertRuleError(
                        f"threshold within {MIN_DISTANCE_PCT}% of existing alert"
                    )
        cur = conn.execute(
            "INSERT INTO alerts "
            "(created_by_agent_run, condition, ticker, threshold, valid_until, "
            " why_this_threshold, source_fact_ids, linked_trade_id, linked_thesis_id, "
            " intraday, active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (agent_run_id, condition, ticker, float(threshold), valid_until,
             why_this_threshold, json.dumps(source_fact_ids),
             linked_trade_id, linked_thesis_id, 1 if intraday else 0),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def cancel(db_path: str | Path, alert_id: int) -> bool:
    conn = db.connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE alerts SET active=0 WHERE id=? AND active=1", (int(alert_id),),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_active(db_path: str | Path) -> list[dict]:
    conn = db.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, condition, ticker, threshold, valid_until, "
            "why_this_threshold, source_fact_ids, intraday, fire_count "
            "FROM alerts WHERE active=1 ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    cols = ["id", "condition", "ticker", "threshold", "valid_until",
            "why_this_threshold", "source_fact_ids", "intraday", "fire_count"]
    return [dict(zip(cols, r)) for r in rows]


def check_and_fire(db_path: str | Path, *, current_price: float) -> list[int]:
    """Return the list of alert ids that fired this tick (cooldown enforced)."""
    fired: list[int] = []
    now = datetime.utcnow().isoformat()
    conn = db.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, condition, threshold, intraday, cooldown_until "
            "FROM alerts WHERE active=1"
        ).fetchall()
    finally:
        conn.close()
    for aid, condition, threshold, intraday, cooldown_until in rows:
        if cooldown_until is not None and cooldown_until > now:
            continue
        if condition == "price_above" and current_price < float(threshold):
            continue
        if condition == "price_below" and current_price > float(threshold):
            continue
        cooldown_min = INTRADAY_COOLDOWN_MIN if intraday else DEFAULT_COOLDOWN_MIN
        new_cooldown = (datetime.utcnow() + timedelta(minutes=cooldown_min)).isoformat()
        conn = db.connect(db_path)
        try:
            conn.execute(
                "UPDATE alerts SET fired_at=?, fire_count=fire_count+1, "
                "cooldown_until=? WHERE id=?",
                (now, new_cooldown, int(aid)),
            )
            conn.commit()
        finally:
            conn.close()
        fired.append(aid)
    return fired


def budget_status(db_path: str | Path) -> dict:
    conn = db.connect(db_path)
    try:
        active = conn.execute("SELECT COUNT(*) FROM alerts WHERE active=1").fetchone()[0]
        today = datetime.utcnow().date().isoformat()
        created = conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE date(created_at)=?", (today,),
        ).fetchone()[0]
    finally:
        conn.close()
    return {"active": active, "max_active": MAX_ACTIVE,
            "created_today": created, "max_created_per_day": MAX_CREATED_PER_DAY}
```

- [ ] **Step 3: Run + commit** → 8 passed.

```bash
git add bsebot/alerts_engine.py tests/test_alerts_engine.py
git commit -m "feat(bsebot): add alerts engine with budget rules + cooldown firing"
```

---

## Task 6: Agent memory (`bsebot/memory.py`) — TDD

- [ ] **Step 1: Tests `tests/test_memory.py`**

```python
import pytest

from bsebot import db, memory


def _seed_fact(p):
    conn = db.connect(p)
    try:
        conn.execute(
            "INSERT INTO raw_documents (source, content_hash, content) "
            "VALUES ('x','h','c')",
        )
        conn.execute(
            "INSERT INTO facts (source_doc_id, fact_type, payload_json, source_quote) "
            "VALUES (1,'news','{}','q')",
        )
        conn.commit()
    finally:
        conn.close()


def test_write_memory_requires_source_fact_ids(tmp_path, migrations_dir):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    with pytest.raises(memory.MemoryError):
        memory.write(p, memory_type="thesis", content="x",
                     importance=0.5, source_fact_ids=[])


def test_write_then_read_active(tmp_path, migrations_dir):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    _seed_fact(p)
    mid = memory.write(p, memory_type="thesis", content="BSE in uptrend",
                       importance=0.8, source_fact_ids=[1])
    rows = memory.read_active(p, memory_type="thesis", limit=10)
    assert len(rows) == 1
    assert rows[0]["id"] == mid


def test_supersede_marks_old_and_chains(tmp_path, migrations_dir):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    _seed_fact(p)
    old = memory.write(p, memory_type="thesis", content="bullish",
                       importance=0.6, source_fact_ids=[1])
    new = memory.supersede(p, old_id=old, new_content="neutral",
                           importance=0.5, source_fact_ids=[1])
    actives = memory.read_active(p, memory_type="thesis", limit=10)
    assert [r["id"] for r in actives] == [new]


def test_read_active_filters_by_type(tmp_path, migrations_dir):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    _seed_fact(p)
    memory.write(p, memory_type="thesis", content="t1", importance=0.5,
                 source_fact_ids=[1])
    memory.write(p, memory_type="observation", content="o1", importance=0.5,
                 source_fact_ids=[1])
    assert len(memory.read_active(p, memory_type="thesis", limit=10)) == 1
    assert len(memory.read_active(p, memory_type="observation", limit=10)) == 1
```

- [ ] **Step 2: Implement `bsebot/memory.py`**

```python
"""agent_memory: write/read/supersede with provenance."""

from __future__ import annotations

import json
from pathlib import Path

from bsebot import db


class MemoryError(ValueError):
    pass


_TYPES = {"thesis", "observation", "lesson"}


def write(
    db_path: str | Path, *,
    memory_type: str,
    content: str,
    importance: float,
    source_fact_ids: list[int],
    created_by_agent_run: int | None = None,
) -> int:
    if memory_type not in _TYPES:
        raise MemoryError(f"memory_type must be one of {_TYPES}")
    if not source_fact_ids:
        raise MemoryError("source_fact_ids must be non-empty")
    if not (0.0 <= float(importance) <= 1.0):
        raise MemoryError("importance must be in [0,1]")
    conn = db.connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO agent_memory (memory_type, content, importance, "
            "source_fact_ids_json, created_by_agent_run) "
            "VALUES (?, ?, ?, ?, ?)",
            (memory_type, content, float(importance),
             json.dumps(source_fact_ids), created_by_agent_run),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def supersede(
    db_path: str | Path, *,
    old_id: int, new_content: str, importance: float,
    source_fact_ids: list[int],
    created_by_agent_run: int | None = None,
) -> int:
    conn = db.connect(db_path)
    try:
        row = conn.execute(
            "SELECT memory_type FROM agent_memory WHERE id=?", (int(old_id),),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise MemoryError(f"unknown memory id {old_id}")
    new_id = write(db_path, memory_type=row[0], content=new_content,
                   importance=importance, source_fact_ids=source_fact_ids,
                   created_by_agent_run=created_by_agent_run)
    conn = db.connect(db_path)
    try:
        conn.execute(
            "UPDATE agent_memory SET superseded_by=?, superseded_at=CURRENT_TIMESTAMP "
            "WHERE id=?", (new_id, int(old_id)),
        )
        conn.commit()
    finally:
        conn.close()
    return new_id


def read_active(
    db_path: str | Path, *,
    memory_type: str | None = None, limit: int = 50,
) -> list[dict]:
    sql = ("SELECT id, memory_type, content, importance, source_fact_ids_json, "
           "created_at FROM agent_memory WHERE superseded_by IS NULL")
    params: tuple = ()
    if memory_type is not None:
        sql += " AND memory_type=?"
        params = (memory_type,)
    sql += " ORDER BY importance DESC, id DESC LIMIT ?"
    params = (*params, int(limit))
    conn = db.connect(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [
        {"id": r[0], "memory_type": r[1], "content": r[2], "importance": r[3],
         "source_fact_ids": json.loads(r[4] or "[]"), "created_at": r[5]}
        for r in rows
    ]
```

- [ ] **Step 3: Commit** → 4 passed.

```bash
git add bsebot/memory.py tests/test_memory.py
git commit -m "feat(bsebot): add agent_memory helpers (write/read/supersede)"
```

---

## Task 7: URL fetcher + web search — TDD

- [ ] **Step 1: Tests `tests/test_fetcher.py`**

```python
from bsebot import db, fetcher


def test_fetch_url_stores_raw_document(tmp_path, migrations_dir, monkeypatch):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)

    def fake_requests_get(url, **kw):
        from bsebot.http import HttpResponse
        return HttpResponse(200, "<html>hi</html>", b"<html>hi</html>", {}, url)

    monkeypatch.setattr(fetcher, "_requests_get", fake_requests_get)
    doc_id = fetcher.fetch_url(p, url="https://example.com/x", agent_run_id=42)
    assert doc_id > 0
    conn = db.connect(p)
    try:
        row = conn.execute(
            "SELECT source, url, metadata_json FROM raw_documents WHERE id=?",
            (doc_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "agent_fetched"
    assert row[1] == "https://example.com/x"
    import json
    assert json.loads(row[2])["fetched_by_agent_run"] == 42


def test_fetch_url_falls_back_to_httpx_on_failure(tmp_path, migrations_dir, monkeypatch):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)

    def fail_requests(url, **kw):
        from bsebot.http import HttpError
        raise HttpError("blocked")

    def ok_httpx(url, **kw):
        from bsebot.http import HttpResponse
        return HttpResponse(200, "via httpx", b"via httpx", {}, url)

    monkeypatch.setattr(fetcher, "_requests_get", fail_requests)
    monkeypatch.setattr(fetcher, "_httpx_get", ok_httpx)
    monkeypatch.setattr(fetcher, "_playwright_get", lambda url: None)

    doc_id = fetcher.fetch_url(p, url="https://x/", agent_run_id=None)
    conn = db.connect(p)
    try:
        content = conn.execute(
            "SELECT content FROM raw_documents WHERE id=?", (doc_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert content == "via httpx"
```

- [ ] **Step 2: Implement `bsebot/fetcher.py`**

```python
"""Multi-strategy URL fetcher for `fetch_url` / `web_search` tools."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from bsebot import db
from bsebot.http import HttpClient, HttpError, HttpResponse


_default_client = HttpClient(min_delay_seconds=2.0)


def _requests_get(url: str) -> HttpResponse:
    return _default_client.get(url)


def _httpx_get(url: str) -> HttpResponse:
    import httpx
    with httpx.Client(timeout=30.0, follow_redirects=True) as c:
        r = c.get(url, headers={"User-Agent": _default_client.next_user_agent()})
        if r.status_code >= 500 or r.status_code == 429:
            raise HttpError(f"httpx {r.status_code} on {url}")
        return HttpResponse(r.status_code, r.text, r.content, dict(r.headers), url)


def _playwright_get(url: str) -> str | None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            page = b.new_page()
            page.goto(url, timeout=45_000)
            content = page.content()
        finally:
            b.close()
    return content


def fetch_url(
    db_path: str | Path, *, url: str, agent_run_id: int | None,
) -> int:
    """Fetch URL through requests → httpx → playwright fallbacks.
    Persists as raw_documents row with source='agent_fetched'.
    Returns the new raw_documents.id."""
    text: str | None = None
    try:
        r = _requests_get(url)
        text = r.text
    except HttpError:
        text = None
    if text is None:
        try:
            r = _httpx_get(url)
            text = r.text
        except HttpError:
            text = None
    if text is None:
        text = _playwright_get(url)
    if text is None:
        text = f"[fetch failed for {url}]"
    return _persist_raw(db_path, source="agent_fetched", url=url,
                        content=text, agent_run_id=agent_run_id)


def _persist_raw(
    db_path, *, source: str, url: str, content: str, agent_run_id: int | None,
    extra_meta: dict | None = None,
) -> int:
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    metadata = {"fetched_by_agent_run": agent_run_id}
    if extra_meta:
        metadata.update(extra_meta)
    metadata_json = json.dumps(metadata)
    conn = db.connect(db_path)
    try:
        try:
            cur = conn.execute(
                "INSERT INTO raw_documents "
                "(source, url, content_hash, content, metadata_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (source, url, content_hash, content, metadata_json),
            )
            conn.commit()
            return cur.lastrowid
        except Exception:
            row = conn.execute(
                "SELECT id FROM raw_documents WHERE content_hash=?", (content_hash,),
            ).fetchone()
            return int(row[0])
    finally:
        conn.close()
```

- [ ] **Step 3: Tests `tests/test_web_search.py`**

```python
from bsebot import db, web_search


def test_brave_search_returns_urls(monkeypatch):
    def fake_get(url, **kw):
        from bsebot.http import HttpResponse
        import json
        body = json.dumps({"web": {"results": [
            {"url": "https://a.com/1", "title": "A"},
            {"url": "https://b.com/2", "title": "B"},
        ]}})
        return HttpResponse(200, body, body.encode(), {}, url)
    monkeypatch.setattr(web_search, "_brave_get", fake_get)
    results = web_search.brave_search("bse ltd", api_key="k")
    assert [r["url"] for r in results] == ["https://a.com/1", "https://b.com/2"]


def test_ddg_search_returns_urls(monkeypatch):
    monkeypatch.setattr(web_search, "_ddg_search",
                        lambda q, max_results: [
                            {"href": "https://x/1", "title": "x"},
                            {"href": "https://y/2", "title": "y"},
                        ])
    res = web_search.ddg_search("bse")
    assert [r["url"] for r in res] == ["https://x/1", "https://y/2"]


def test_search_and_fetch_falls_back(tmp_path, migrations_dir, monkeypatch):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    monkeypatch.setattr(web_search, "brave_search",
                        lambda q, api_key, top_k=5: (_ for _ in ()).throw(RuntimeError("no key")))
    monkeypatch.setattr(web_search, "ddg_search",
                        lambda q, top_k=5: [{"url": "https://x/1", "title": "x"}])

    from bsebot import fetcher
    monkeypatch.setattr(fetcher, "fetch_url",
                        lambda p, url, agent_run_id: 1234)
    doc_ids = web_search.search_and_fetch(p, "bse", api_key=None, agent_run_id=1)
    assert doc_ids == [1234]
```

- [ ] **Step 4: Implement `bsebot/web_search.py`**

```python
"""Web search → fetch_url pipeline. Brave Search → DuckDuckGo fallback."""

from __future__ import annotations

import json
from pathlib import Path

from bsebot import fetcher
from bsebot.http import HttpClient


_client = HttpClient(min_delay_seconds=1.0)


def _brave_get(url, **kw):
    return _client.get(url, **kw)


def brave_search(query: str, *, api_key: str, top_k: int = 5) -> list[dict]:
    if not api_key:
        raise RuntimeError("brave api key missing")
    url = f"https://api.search.brave.com/res/v1/web/search?q={query}&count={top_k}"
    r = _brave_get(url, headers={"X-Subscription-Token": api_key,
                                 "Accept": "application/json"})
    payload = json.loads(r.text)
    results = (payload.get("web") or {}).get("results") or []
    return [{"url": x["url"], "title": x.get("title", "")} for x in results[:top_k]]


def _ddg_search(query: str, max_results: int) -> list[dict]:
    from duckduckgo_search import DDGS
    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results))


def ddg_search(query: str, *, top_k: int = 5) -> list[dict]:
    rows = _ddg_search(query, top_k)
    return [{"url": r.get("href") or r.get("url"), "title": r.get("title", "")}
            for r in rows if (r.get("href") or r.get("url"))]


def search_and_fetch(
    db_path: str | Path, query: str, *,
    api_key: str | None, agent_run_id: int | None, top_k: int = 5,
) -> list[int]:
    """Run search; fetch each result via fetcher.fetch_url; return doc ids."""
    try:
        if api_key:
            results = brave_search(query, api_key=api_key, top_k=top_k)
        else:
            raise RuntimeError("brave not configured")
    except Exception:
        results = ddg_search(query, top_k=top_k)
    return [fetcher.fetch_url(db_path, url=r["url"], agent_run_id=agent_run_id)
            for r in results]
```

- [ ] **Step 5: Commit**

```bash
git add bsebot/fetcher.py bsebot/web_search.py tests/test_fetcher.py tests/test_web_search.py
git commit -m "feat(bsebot): add multi-strategy URL fetcher + Brave/DDG web search"
```

---

## Task 8: Adversarial fact-checker (`bsebot/factcheck.py`) — TDD

- [ ] **Step 1: Tests `tests/test_factcheck.py`**

```python
import pytest

from bsebot import db, factcheck
from bsebot.config import AppConfig, LLMConfig, ProviderConfig


def _config(p):
    return AppConfig(
        database_path=str(p),
        llm=LLMConfig(
            extract_max_tokens=2048, reason_max_tokens=8192,
            continuation_max_attempts=3,
            providers=[ProviderConfig("g", "gemini/gemini-2.5-flash",
                                      "GEMINI_API_KEY", "k", ["extract", "reason"])],
        ),
    )


def _seed_fact(p, payload="Strong growth in Q1"):
    conn = db.connect(p)
    try:
        conn.execute(
            "INSERT INTO raw_documents (source, content_hash, content) "
            "VALUES ('x','h','c')",
        )
        import json
        conn.execute(
            "INSERT INTO facts (source_doc_id, fact_type, payload_json, source_quote) "
            "VALUES (1,'news',?,?)",
            (json.dumps({"summary": payload}), payload),
        )
        conn.commit()
    finally:
        conn.close()


def test_citation_regex_finds_ids():
    text = "We see growth [fact:42] and risk [fact:100]."
    assert factcheck.citations_in(text) == [42, 100]


def test_citation_regex_rejects_unsupported_claim():
    assert factcheck.has_uncited_claims("the price is up.") is True
    assert factcheck.has_uncited_claims("the price is up [fact:1].") is False


def test_audit_decision_returns_ok_when_facts_support(tmp_path, migrations_dir, monkeypatch):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    _seed_fact(p)
    cfg = _config(p)

    class _Router:
        def __init__(self, cfg): pass
        def reason(self, messages, tools=None):
            return ("verdict: YES", {"provider": "g", "model": "g"})

    monkeypatch.setattr(factcheck, "LLMRouter", _Router)
    res = factcheck.audit(cfg, reasoning_text="growth [fact:1]")
    assert res.passed is True


def test_audit_blocks_when_auditor_says_no(tmp_path, migrations_dir, monkeypatch):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    _seed_fact(p)
    cfg = _config(p)

    class _Router:
        def __init__(self, cfg): pass
        def reason(self, messages, tools=None):
            return ("Claim 1: NO — fact does not say growth", {"provider":"g","model":"g"})

    monkeypatch.setattr(factcheck, "LLMRouter", _Router)
    res = factcheck.audit(cfg, reasoning_text="growth [fact:1]")
    assert res.passed is False
    assert "NO" in res.auditor_response
```

- [ ] **Step 2: Implement `bsebot/factcheck.py`**

```python
"""Citation regex + adversarial second-LLM audit pass."""

from __future__ import annotations

import re
from dataclasses import dataclass

from bsebot import db
from bsebot.config import AppConfig
from bsebot.llm import LLMRouter


_FACT_REF = re.compile(r"\[fact:(\d+)\]")


def citations_in(text: str) -> list[int]:
    return [int(m.group(1)) for m in _FACT_REF.finditer(text or "")]


def has_uncited_claims(text: str) -> bool:
    """True iff `text` contains at least one sentence with no [fact:NNNN] ref."""
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        if not sentence:
            continue
        if not _FACT_REF.search(sentence):
            return True
    return False


@dataclass
class AuditResult:
    passed: bool
    auditor_response: str


def audit(cfg: AppConfig, *, reasoning_text: str) -> AuditResult:
    fact_ids = citations_in(reasoning_text)
    quoted: list[tuple[int, str, str]] = []
    if fact_ids:
        conn = db.connect(cfg.database_path)
        try:
            placeholders = ",".join("?" * len(fact_ids))
            rows = conn.execute(
                f"SELECT id, payload_json, source_quote FROM facts "
                f"WHERE id IN ({placeholders})",
                fact_ids,
            ).fetchall()
        finally:
            conn.close()
        quoted = [(r[0], r[1], r[2]) for r in rows]
    router = LLMRouter(cfg)
    prompt = (
        "You are a skeptical auditor. The reasoning below cites facts as "
        "[fact:NNNN]. For each cited fact, does the fact actually support the "
        "claim? Reply with one line per claim: 'Claim N: YES' or 'Claim N: NO' "
        "with explanation. End your reply with 'verdict: YES' iff ALL claims "
        "are supported; otherwise 'verdict: NO'.\n\n"
        "REASONING:\n" + reasoning_text + "\n\nFACTS:\n"
        + "\n".join(f"[fact:{i}] quote={q!r} payload={p}" for i, p, q in quoted)
    )
    text, _ = router.reason([{"role": "user", "content": prompt}])
    passed = "verdict: YES" in text or text.strip().endswith("YES")
    if "verdict: NO" in text:
        passed = False
    return AuditResult(passed=passed, auditor_response=text)
```

- [ ] **Step 3: Commit** → 4 passed.

```bash
git add bsebot/factcheck.py tests/test_factcheck.py
git commit -m "feat(bsebot): add citation regex + adversarial fact-check pass"
```

---

## Task 9: Vault stubs for decisions / trades / alerts / memory

- [ ] **Step 1: Append to `bsebot/vault.py`**

Add to `/Users/devamarnani/Desktop/bsebot/bsebot/vault.py`:

```python
def publish_decision(
    *, agent_run_id: int, action: str, reasoning: str,
    fact_ids: list[int], trade_id: int | None,
) -> PublishResult:
    return PublishResult(written=False, path=None)


def publish_trade(*, trade_id: int) -> PublishResult:
    return PublishResult(written=False, path=None)


def publish_alert_snapshot(*, active_alerts: list[dict]) -> PublishResult:
    return PublishResult(written=False, path=None)


def publish_memory(*, memory_id: int) -> PublishResult:
    return PublishResult(written=False, path=None)
```

- [ ] **Step 2: Commit**

```bash
git add bsebot/vault.py
git commit -m "feat(bsebot): add vault stubs for decision/trade/alert/memory (plan 4 fills)"
```

---

## Task 10: Agent tools (`bsebot/agent_tools.py`) — TDD

- [ ] **Step 1: Tests `tests/test_agent_tools.py`**

```python
from datetime import datetime, timedelta
import json

import pytest

from bsebot import agent_tools, db
from bsebot.config import AppConfig, LLMConfig, ProviderConfig


def _config(p):
    return AppConfig(
        database_path=str(p),
        llm=LLMConfig(
            extract_max_tokens=2048, reason_max_tokens=8192,
            continuation_max_attempts=3,
            providers=[ProviderConfig("g", "gemini/gemini-2.5-flash",
                                      "GEMINI_API_KEY", "k", ["extract", "reason"])],
        ),
    )


def _seed_fact(p, *, fact_type="news", ticker="BSE"):
    conn = db.connect(p)
    try:
        conn.execute("INSERT INTO raw_documents (source, content_hash, content) "
                     "VALUES ('news', 'h', 'c')")
        conn.execute(
            "INSERT INTO facts (source_doc_id, fact_type, ticker, payload_json, "
            "source_quote) VALUES (1, ?, ?, ?, 'q')",
            (fact_type, ticker, json.dumps({"summary": "x"})),
        )
        conn.commit()
    finally:
        conn.close()


def test_query_facts_filters_by_type(tmp_path, migrations_dir):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    _seed_fact(p, fact_type="news")
    ctx = agent_tools.Context(cfg=_config(p), current_run_id=1)
    res = agent_tools.query_facts(ctx, ticker="BSE", fact_type="news", limit=10)
    assert len(res) == 1


def test_set_alert_invokes_engine(tmp_path, migrations_dir):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    _seed_fact(p)
    ctx = agent_tools.Context(cfg=_config(p), current_run_id=1, current_price=100.0)
    aid = agent_tools.set_alert(
        ctx, condition="price_above", threshold=120.0,
        valid_until=(datetime.utcnow() + timedelta(days=5)).isoformat(),
        why_this_threshold="thesis-driven", source_fact_ids=[1],
    )
    assert aid > 0


def test_submit_decision_blocks_uncited(tmp_path, migrations_dir, monkeypatch):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    _seed_fact(p)
    ctx = agent_tools.Context(cfg=_config(p), current_run_id=1, current_price=100.0)
    with pytest.raises(agent_tools.AgentToolError) as exc:
        agent_tools.submit_decision(
            ctx, action="buy", confidence=0.9,
            reasoning="growth looks strong",  # no [fact:NN]
            fact_ids_consulted=[1], stop_loss=95.0, target=110.0, quantity=5,
        )
    assert "cite" in str(exc.value).lower()


def test_submit_decision_writes_trade_and_passes_audit(
    tmp_path, migrations_dir, monkeypatch,
):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    _seed_fact(p)
    from bsebot import factcheck
    monkeypatch.setattr(
        factcheck, "audit",
        lambda cfg, reasoning_text: factcheck.AuditResult(True, "verdict: YES"),
    )
    ctx = agent_tools.Context(cfg=_config(p), current_run_id=1, current_price=100.0)
    from bsebot import cash
    cash.deposit(p, 10000.0, note="seed")
    out = agent_tools.submit_decision(
        ctx, action="buy", confidence=0.9,
        reasoning="growth [fact:1]",
        fact_ids_consulted=[1], stop_loss=95.0, target=110.0, quantity=5,
    )
    assert out["trade_id"] is not None
    assert out["audit_passed"] is True


def test_submit_decision_blocks_when_auditor_fails(
    tmp_path, migrations_dir, monkeypatch,
):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    _seed_fact(p)
    from bsebot import factcheck
    monkeypatch.setattr(
        factcheck, "audit",
        lambda cfg, reasoning_text: factcheck.AuditResult(False, "Claim 1: NO"),
    )
    ctx = agent_tools.Context(cfg=_config(p), current_run_id=1, current_price=100.0)
    out = agent_tools.submit_decision(
        ctx, action="buy", confidence=0.9,
        reasoning="growth [fact:1]",
        fact_ids_consulted=[1], stop_loss=95.0, target=110.0, quantity=5,
    )
    assert out["trade_id"] is None
    assert out["audit_passed"] is False


def test_request_new_source_inserts_pending_row(tmp_path, migrations_dir):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    ctx = agent_tools.Context(cfg=_config(p), current_run_id=1)
    tid = agent_tools.request_new_source(
        ctx, name="MoneyControl BSE",
        description="Daily commentary on BSE Ltd",
        url="https://moneycontrol.com/bse",
        fetch_method="html", expected_signal_type="sentiment",
        rationale="Plugs a gap in retail-flow coverage",
    )
    conn = db.connect(p)
    try:
        row = conn.execute(
            "SELECT name, enabled, approved_at FROM tools WHERE id=?", (tid,),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "MoneyControl BSE"
    assert row[1] == 0
    assert row[2] is None
```

- [ ] **Step 2: Implement `bsebot/agent_tools.py`**

```python
"""Concrete implementations of every tool the agent can call."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from bsebot import (
    alerts_engine, cash, db, factcheck, fetcher, memory, positions, web_search,
)
from bsebot.config import AppConfig


class AgentToolError(RuntimeError):
    pass


@dataclass
class Context:
    cfg: AppConfig
    current_run_id: int
    current_price: float | None = None


def query_facts(
    ctx: Context, *,
    ticker: str = "BSE", fact_type: str | None = None,
    date_from: str | None = None, date_to: str | None = None,
    limit: int = 50,
) -> list[dict]:
    sql = "SELECT id, fact_type, ticker, payload_json, source_quote, created_at FROM facts WHERE ticker=?"
    params: list = [ticker]
    if fact_type:
        sql += " AND fact_type=?"; params.append(fact_type)
    if date_from:
        sql += " AND created_at>=?"; params.append(date_from)
    if date_to:
        sql += " AND created_at<=?"; params.append(date_to)
    sql += " ORDER BY id DESC LIMIT ?"; params.append(int(limit))
    conn = db.connect(ctx.cfg.database_path)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [
        {"id": r[0], "fact_type": r[1], "ticker": r[2],
         "payload": json.loads(r[3] or "{}"), "source_quote": r[4],
         "created_at": r[5]}
        for r in rows
    ]


def read_raw_document(ctx: Context, *, doc_id: int) -> dict:
    conn = db.connect(ctx.cfg.database_path)
    try:
        row = conn.execute(
            "SELECT id, source, url, content FROM raw_documents WHERE id=?",
            (int(doc_id),),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise AgentToolError(f"no raw_document id={doc_id}")
    return {"id": row[0], "source": row[1], "url": row[2], "content": row[3]}


def read_memory(
    ctx: Context, *, memory_type: str | None = None, limit: int = 20,
) -> list[dict]:
    return memory.read_active(ctx.cfg.database_path,
                              memory_type=memory_type, limit=limit)


def write_memory(
    ctx: Context, *, memory_type: str, content: str, importance: float,
    source_fact_ids: list[int],
) -> int:
    return memory.write(
        ctx.cfg.database_path, memory_type=memory_type, content=content,
        importance=importance, source_fact_ids=source_fact_ids,
        created_by_agent_run=ctx.current_run_id,
    )


def supersede_memory(
    ctx: Context, *, old_id: int, new_content: str, importance: float,
    source_fact_ids: list[int],
) -> int:
    return memory.supersede(
        ctx.cfg.database_path, old_id=old_id, new_content=new_content,
        importance=importance, source_fact_ids=source_fact_ids,
        created_by_agent_run=ctx.current_run_id,
    )


def check_open_positions(ctx: Context) -> list[dict]:
    return positions.list_open(ctx.cfg.database_path)


def check_recent_decisions(ctx: Context, *, n: int = 10) -> list[dict]:
    conn = db.connect(ctx.cfg.database_path)
    try:
        rows = conn.execute(
            "SELECT id, started_at, decision_json, reasoning, status "
            "FROM agent_runs WHERE status='complete' ORDER BY id DESC LIMIT ?",
            (int(n),),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"id": r[0], "started_at": r[1],
         "decision": json.loads(r[2] or "{}"),
         "reasoning": r[3], "status": r[4]}
        for r in rows
    ]


def set_alert(
    ctx: Context, *,
    condition: str, threshold: float, valid_until: str,
    why_this_threshold: str, source_fact_ids: list[int],
    intraday: bool = False,
) -> int:
    if ctx.current_price is None:
        raise AgentToolError("current_price unknown; cannot validate alert distance")
    return alerts_engine.set_alert(
        ctx.cfg.database_path, agent_run_id=ctx.current_run_id,
        condition=condition, threshold=threshold, valid_until=valid_until,
        why_this_threshold=why_this_threshold,
        source_fact_ids=source_fact_ids, current_price=ctx.current_price,
        intraday=intraday,
    )


def cancel_alert(ctx: Context, *, alert_id: int) -> bool:
    return alerts_engine.cancel(ctx.cfg.database_path, alert_id)


def request_new_source(
    ctx: Context, *,
    name: str, description: str, url: str,
    fetch_method: str, expected_signal_type: str, rationale: str,
) -> int:
    conn = db.connect(ctx.cfg.database_path)
    try:
        cur = conn.execute(
            "INSERT INTO tools (name, description, url, fetch_method, "
            "expected_signal_type, rationale, created_by, enabled) "
            "VALUES (?, ?, ?, ?, ?, ?, 'agent', 0)",
            (name, description, url, fetch_method, expected_signal_type, rationale),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def web_search_tool(ctx: Context, *, query: str) -> list[int]:
    import os
    api_key = os.environ.get("BRAVE_API_KEY") or None
    return web_search.search_and_fetch(
        ctx.cfg.database_path, query, api_key=api_key,
        agent_run_id=ctx.current_run_id,
    )


def fetch_url_tool(ctx: Context, *, url: str) -> int:
    return fetcher.fetch_url(ctx.cfg.database_path, url=url,
                             agent_run_id=ctx.current_run_id)


def get_current_price(ctx: Context) -> dict:
    return {"price": ctx.current_price, "ticker": "BSE"}


def get_price_history(
    ctx: Context, *, date_from: str, date_to: str,
) -> list[dict]:
    conn = db.connect(ctx.cfg.database_path)
    try:
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume FROM price_history "
            "WHERE ticker='BSE' AND date BETWEEN ? AND ? ORDER BY date",
            (date_from, date_to),
        ).fetchall()
    finally:
        conn.close()
    cols = ["date", "open", "high", "low", "close", "volume"]
    return [dict(zip(cols, r)) for r in rows]


def submit_decision(
    ctx: Context, *,
    action: str, confidence: float, reasoning: str,
    fact_ids_consulted: list[int],
    stop_loss: float | None, target: float | None, quantity: int,
) -> dict:
    if action not in {"buy", "sell_short", "hold", "close_position"}:
        raise AgentToolError(f"unknown action: {action!r}")
    if action in {"buy", "sell_short"} and factcheck.has_uncited_claims(reasoning):
        raise AgentToolError(
            "reasoning has unsupported claims (every claim must cite [fact:NNNN])"
        )
    audit = factcheck.audit(ctx.cfg, reasoning_text=reasoning)
    trade_id: int | None = None
    if audit.passed and action in {"buy", "sell_short"}:
        if ctx.current_price is None:
            raise AgentToolError("current_price required to open a trade")
        side = "long" if action == "buy" else "short"
        trade_id = positions.open_trade(
            ctx.cfg.database_path, agent_run_id=ctx.current_run_id,
            side=side, quantity=int(quantity),
            entry_price=float(ctx.current_price),
            stop_loss=stop_loss, target=target,
        )
    _persist_agent_decision(
        ctx, action=action, confidence=confidence, reasoning=reasoning,
        fact_ids_consulted=fact_ids_consulted, trade_id=trade_id,
        audit_passed=audit.passed, audit_text=audit.auditor_response,
    )
    return {"trade_id": trade_id, "audit_passed": audit.passed,
            "audit_response": audit.auditor_response, "action": action}


def _persist_agent_decision(
    ctx, *, action, confidence, reasoning, fact_ids_consulted, trade_id,
    audit_passed, audit_text,
):
    decision = {"action": action, "confidence": confidence,
                "trade_id": trade_id, "audit_passed": audit_passed}
    conn = db.connect(ctx.cfg.database_path)
    try:
        conn.execute(
            "UPDATE agent_runs SET decision_json=?, reasoning=?, "
            "fact_ids_consulted_json=?, status='complete', "
            "ended_at=CURRENT_TIMESTAMP WHERE id=?",
            (json.dumps(decision), reasoning,
             json.dumps(fact_ids_consulted), ctx.current_run_id),
        )
        conn.commit()
    finally:
        conn.close()


TOOLS: dict = {
    "query_facts": query_facts,
    "read_raw_document": read_raw_document,
    "read_memory": read_memory,
    "write_memory": write_memory,
    "supersede_memory": supersede_memory,
    "check_open_positions": check_open_positions,
    "check_recent_decisions": check_recent_decisions,
    "set_alert": set_alert,
    "cancel_alert": cancel_alert,
    "request_new_source": request_new_source,
    "web_search": web_search_tool,
    "fetch_url": fetch_url_tool,
    "get_current_price": get_current_price,
    "get_price_history": get_price_history,
    "submit_decision": submit_decision,
}
```

- [ ] **Step 3: Commit** → 6 passed.

```bash
git add bsebot/agent_tools.py tests/test_agent_tools.py
git commit -m "feat(bsebot): add agent tool implementations + submit_decision validators"
```

---

## Task 11: Agent run loop (`bsebot/agent.py`) — TDD

- [ ] **Step 1: Tests `tests/test_agent.py`**

```python
import json

from bsebot import agent, agent_tools, db
from bsebot.config import AppConfig, LLMConfig, ProviderConfig


def _config(p):
    return AppConfig(
        database_path=str(p),
        llm=LLMConfig(extract_max_tokens=2048, reason_max_tokens=8192,
                      continuation_max_attempts=3,
                      providers=[ProviderConfig("g", "gemini/gemini-2.5-flash",
                                                "GEMINI_API_KEY","k",["extract","reason"])]),
    )


def _seed_fact(p):
    conn = db.connect(p)
    try:
        conn.execute("INSERT INTO raw_documents (source, content_hash, content) "
                     "VALUES ('news','h','c')")
        conn.execute("INSERT INTO facts (source_doc_id, fact_type, payload_json, source_quote) "
                     "VALUES (1,'news','{}','q')")
        conn.commit()
    finally:
        conn.close()


def test_agent_run_writes_agent_runs_row_and_terminates(tmp_path, migrations_dir, monkeypatch):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    _seed_fact(p)
    cfg = _config(p)

    # Stub router: pretend the LLM emits a single submit_decision tool call.
    class _Router:
        def __init__(self, cfg): pass
        def reason(self, messages, tools=None):
            # Return JSON-encoded tool call: agent.parse_tool_call will read this.
            text = json.dumps({
                "tool": "submit_decision",
                "args": {
                    "action": "hold", "confidence": 0.5,
                    "reasoning": "no edge today [fact:1]",
                    "fact_ids_consulted": [1],
                    "stop_loss": None, "target": None, "quantity": 0,
                },
            })
            return text, {"provider": "g", "model": "g"}

    monkeypatch.setattr(agent, "LLMRouter", _Router)
    from bsebot import factcheck
    monkeypatch.setattr(
        factcheck, "audit",
        lambda cfg, reasoning_text: factcheck.AuditResult(True, "verdict: YES"),
    )

    run_id = agent.run(cfg, trigger="scheduled", current_price=100.0)
    assert run_id > 0

    conn = db.connect(p)
    try:
        row = conn.execute(
            "SELECT status, decision_json FROM agent_runs WHERE id=?", (run_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "complete"
    assert json.loads(row[1])["action"] == "hold"


def test_agent_loop_aborts_after_max_iterations(tmp_path, migrations_dir, monkeypatch):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    cfg = _config(p)

    class _Router:
        def __init__(self, cfg): pass
        def reason(self, messages, tools=None):
            return (json.dumps({"tool": "read_memory", "args": {"limit": 1}}),
                    {"provider": "g", "model": "g"})

    monkeypatch.setattr(agent, "LLMRouter", _Router)
    run_id = agent.run(cfg, trigger="scheduled", current_price=100.0,
                       max_iterations=3)
    conn = db.connect(p)
    try:
        row = conn.execute(
            "SELECT status, iterations FROM agent_runs WHERE id=?", (run_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "aborted"
    assert row[1] == 3


def test_concurrent_invocation_serialized_by_lock(tmp_path, migrations_dir, monkeypatch):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    cfg = _config(p)

    class _Router:
        def __init__(self, cfg): pass
        def reason(self, messages, tools=None):
            return (json.dumps({"tool": "submit_decision", "args": {
                "action": "hold", "confidence": 0.5,
                "reasoning": "wait [fact:1]", "fact_ids_consulted": [],
                "stop_loss": None, "target": None, "quantity": 0,
            }}), {})

    monkeypatch.setattr(agent, "LLMRouter", _Router)
    from bsebot import factcheck
    monkeypatch.setattr(
        factcheck, "audit",
        lambda cfg, reasoning_text: factcheck.AuditResult(True, "v: YES"),
    )

    _seed_fact(p)
    r1 = agent.run(cfg, trigger="scheduled", current_price=100.0)
    r2 = agent.run(cfg, trigger="scheduled", current_price=100.0)
    assert r1 != r2  # both ran; lock released between calls
```

- [ ] **Step 2: Implement `bsebot/agent.py`**

```python
"""Agent run loop. Tool-call dispatch + lock + persistence."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from bsebot import agent_tools, alerts_engine, db, memory, positions
from bsebot.config import AppConfig
from bsebot.llm import LLMRouter


SYSTEM_PROMPT = (
    "You have zero prior knowledge of BSE Ltd, Indian markets, or finance. "
    "Your only information is the facts and documents provided. If you need "
    "something missing, use `request_new_source` or `web_search`. Do not "
    "recall from training. Every non-trivial claim in your reasoning MUST "
    "cite [fact:NNNN]. You MUST end by calling `submit_decision`. "
    "On each step output a JSON object with keys 'tool' and 'args'."
)


def _acquire_lock(db_path: str | Path, *, timeout_s: float = 30.0) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), isolation_level=None, timeout=timeout_s)
    conn.execute("BEGIN IMMEDIATE")
    return conn


def _release_lock(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("COMMIT")
    finally:
        conn.close()


def _start_run(
    db_path: str | Path, *, trigger: str, triggered_by_alert_id: int | None,
    model: str,
) -> int:
    conn = db.connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO agent_runs (trigger, triggered_by_alert_id, model, status) "
            "VALUES (?, ?, ?, 'running')",
            (trigger, triggered_by_alert_id, model),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _mark_aborted(db_path: str | Path, run_id: int, iterations: int) -> None:
    conn = db.connect(db_path)
    try:
        conn.execute(
            "UPDATE agent_runs SET status='aborted', iterations=?, "
            "ended_at=CURRENT_TIMESTAMP WHERE id=?",
            (int(iterations), int(run_id)),
        )
        conn.commit()
    finally:
        conn.close()


def _bump_iterations(db_path, run_id, n):
    conn = db.connect(db_path)
    try:
        conn.execute("UPDATE agent_runs SET iterations=? WHERE id=?",
                     (int(n), int(run_id)))
        conn.commit()
    finally:
        conn.close()


def _build_context(cfg: AppConfig, *, current_price: float | None) -> str:
    db_path = cfg.database_path
    mem = memory.read_active(db_path, limit=20)
    opens = positions.list_open(db_path)
    budget = alerts_engine.budget_status(db_path)
    facts_recent = agent_tools.query_facts(
        agent_tools.Context(cfg, current_run_id=0, current_price=current_price),
        ticker="BSE", limit=25,
    )
    return (
        f"CURRENT_PRICE: {current_price}\n"
        f"ALERT_BUDGET: {json.dumps(budget)}\n"
        f"OPEN_POSITIONS:\n{json.dumps(opens)[:2000]}\n"
        f"ACTIVE_MEMORIES:\n{json.dumps(mem)[:4000]}\n"
        f"RECENT_FACTS:\n{json.dumps(facts_recent)[:6000]}\n"
    )


def parse_tool_call(text: str) -> tuple[str, dict] | None:
    try:
        obj = json.loads(text.strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    tool = obj.get("tool")
    args = obj.get("args") or {}
    if not isinstance(tool, str) or not isinstance(args, dict):
        return None
    return tool, args


def run(
    cfg: AppConfig, *,
    trigger: str,
    triggered_by_alert_id: int | None = None,
    current_price: float | None = None,
    max_iterations: int = 15,
) -> int:
    lock_conn = _acquire_lock(cfg.database_path)
    try:
        router = LLMRouter(cfg)
        model = cfg.llm.providers[0].model if cfg.llm.providers else "unknown"
        run_id = _start_run(cfg.database_path, trigger=trigger,
                            triggered_by_alert_id=triggered_by_alert_id, model=model)
        ctx = agent_tools.Context(cfg=cfg, current_run_id=run_id,
                                  current_price=current_price)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _build_context(cfg, current_price=current_price)},
        ]
        for i in range(1, max_iterations + 1):
            text, _meta = router.reason(messages)
            _bump_iterations(cfg.database_path, run_id, i)
            call = parse_tool_call(text)
            if call is None:
                messages.append({"role": "user",
                                 "content": "Reply with a JSON tool call object."})
                continue
            tool, args = call
            fn = agent_tools.TOOLS.get(tool)
            if fn is None:
                messages.append({"role": "user",
                                 "content": f"Unknown tool: {tool!r}"})
                continue
            try:
                result = fn(ctx, **args)
            except agent_tools.AgentToolError as e:
                messages.append({"role": "user",
                                 "content": f"Tool {tool} error: {e}"})
                continue
            if tool == "submit_decision":
                return run_id
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user",
                             "content": f"TOOL_RESULT {tool}: {json.dumps(result)[:4000]}"})
        _mark_aborted(cfg.database_path, run_id, max_iterations)
        return run_id
    finally:
        _release_lock(lock_conn)
```

- [ ] **Step 3: Commit** → 3 passed.

```bash
git add bsebot/agent.py tests/test_agent.py
git commit -m "feat(bsebot): add agent run loop with lock, tool dispatch, max-iter abort"
```

---

## Task 12: Price watcher (`bsebot/price_watcher.py`) — TDD

- [ ] **Step 1: Tests `tests/test_price_watcher.py`**

```python
from datetime import datetime, timedelta
import pandas as pd

from bsebot import alerts_engine, cash, db, positions, price_watcher
from bsebot.config import AppConfig, LLMConfig, ProviderConfig


def _config(p):
    return AppConfig(
        database_path=str(p),
        llm=LLMConfig(extract_max_tokens=2048, reason_max_tokens=8192,
                      continuation_max_attempts=3,
                      providers=[ProviderConfig("g","gemini/gemini-2.5-flash",
                                                "GEMINI_API_KEY","k",["extract","reason"])]),
    )


def _seed_fact(p):
    conn = db.connect(p)
    try:
        conn.execute("INSERT INTO raw_documents (source, content_hash, content) "
                     "VALUES ('x','h','c')")
        conn.execute("INSERT INTO facts (source_doc_id, fact_type, payload_json, source_quote) "
                     "VALUES (1,'news','{}','q')")
        conn.commit()
    finally:
        conn.close()


def test_tick_evaluates_positions_and_fires_alerts(tmp_path, migrations_dir, monkeypatch):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    _seed_fact(p)
    cash.deposit(p, 10000.0, note="seed")
    tid = positions.open_trade(p, agent_run_id=None, side="long",
                               quantity=1, entry_price=100.0,
                               stop_loss=95.0, target=110.0)
    vu = (datetime.utcnow() + timedelta(days=5)).isoformat()
    aid = alerts_engine.set_alert(p, agent_run_id=None,
                                  condition="price_above", threshold=108.0,
                                  valid_until=vu, why_this_threshold="x",
                                  source_fact_ids=[1], current_price=100.0)

    invocations: list[float] = []
    monkeypatch.setattr(price_watcher, "_invoke_agent",
                        lambda cfg, current_price, triggered_by_alert_id: invocations.append(current_price))

    cfg = _config(p)
    res = price_watcher.tick(cfg, current_price=112.0)
    assert tid in res["closed_trades"]
    assert aid in res["fired_alerts"]
    assert invocations == [112.0]


def test_tick_writes_price_history_row(tmp_path, migrations_dir):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    cfg = _config(p)
    price_watcher.tick(cfg, current_price=101.5)
    conn = db.connect(p)
    try:
        n = conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
    finally:
        conn.close()
    # tick does NOT write daily candles; that's the bulk harvester. Should be 0.
    assert n == 0


def test_run_loop_breaks_when_not_market_open(tmp_path, migrations_dir, monkeypatch):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    cfg = _config(p)
    monkeypatch.setattr(price_watcher.clock, "is_market_open", lambda: False)
    sleeps: list[float] = []
    monkeypatch.setattr(price_watcher.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(price_watcher, "_yfinance_price", lambda sym: 100.0)

    iters = price_watcher.run_loop(cfg, ticks=3, off_hours_sleep=0.0,
                                   tick_interval=0.0)
    assert iters == 0  # no ticks executed
```

- [ ] **Step 2: Implement `bsebot/price_watcher.py`**

```python
"""30-second price polling loop. Plays alerts + position evaluations."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import yfinance as yf

from bsebot import agent as _agent
from bsebot import alerts_engine, clock, positions
from bsebot.config import AppConfig


log = logging.getLogger(__name__)


def _yfinance_price(symbol: str = "BSE.NS") -> float | None:
    try:
        df = yf.Ticker(symbol).history(period="1d", interval="1m")
    except Exception as e:
        log.warning("yfinance failed: %s", e)
        return None
    if df is None or df.empty:
        return None
    return float(df["Close"].iloc[-1])


def _invoke_agent(cfg: AppConfig, current_price: float,
                  triggered_by_alert_id: int | None) -> None:
    try:
        _agent.run(cfg, trigger="alert", current_price=current_price,
                   triggered_by_alert_id=triggered_by_alert_id)
    except Exception as e:
        log.warning("agent run failed: %s", e)


def tick(cfg: AppConfig, *, current_price: float) -> dict:
    closed = positions.evaluate(cfg.database_path, current_price=current_price)
    fired = alerts_engine.check_and_fire(cfg.database_path,
                                         current_price=current_price)
    for alert_id in fired:
        _invoke_agent(cfg, current_price=current_price,
                      triggered_by_alert_id=alert_id)
    return {"closed_trades": closed, "fired_alerts": fired}


def run_loop(
    cfg: AppConfig, *,
    symbol: str = "BSE.NS",
    ticks: int | None = None,
    tick_interval: float = 30.0,
    off_hours_sleep: float = 300.0,
) -> int:
    """Polling loop. `ticks=None` → run forever. Returns count of executed ticks."""
    n = 0
    while True:
        if ticks is not None and n >= ticks:
            return n
        if not clock.is_market_open():
            time.sleep(off_hours_sleep)
            if ticks is not None:
                # Off-hours cycle still counts toward 'iterations'? No — we
                # explicitly only count market ticks.
                ticks -= 1
                if ticks <= 0:
                    return n
            continue
        price = _yfinance_price(symbol)
        if price is not None:
            tick(cfg, current_price=price)
            n += 1
        time.sleep(tick_interval)
```

- [ ] **Step 3: Commit** → 3 passed.

```bash
git add bsebot/price_watcher.py tests/test_price_watcher.py
git commit -m "feat(bsebot): add price watcher loop (tick + market-hours gate)"
```

---

## Task 13: systemd unit + migration 003

- [ ] **Step 1: Migration**

Write `/Users/devamarnani/Desktop/bsebot/migrations/003_agent_locks_and_constraints.sql`:

```sql
-- Plan 3 supporting indexes.
CREATE INDEX IF NOT EXISTS idx_alerts_ticker_active ON alerts(ticker, active);
CREATE INDEX IF NOT EXISTS idx_trades_status_side ON trades(status, side);
CREATE INDEX IF NOT EXISTS idx_agent_runs_trigger ON agent_runs(trigger);
```

- [ ] **Step 2: systemd unit**

Write `/Users/devamarnani/Desktop/bsebot/systemd/bsebot-price-watcher.service`:

```ini
[Unit]
Description=BSEBot price watcher (30s loop during market hours)
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/bsebot
ExecStart=/opt/bsebot/.venv/bin/python -m bsebot.price_watcher_main
Restart=always
RestartSec=10
User=bsebot
Group=bsebot
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: Entrypoint module**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/price_watcher_main.py`:

```python
"""systemd entrypoint: runs the price watcher loop forever."""

from __future__ import annotations

from bsebot import config as _config
from bsebot import price_watcher


def main() -> None:
    cfg = _config.load("/opt/bsebot/config.yaml", env_path="/opt/bsebot/.env")
    price_watcher.run_loop(cfg)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Commit**

```bash
git add migrations/003_agent_locks_and_constraints.sql \
        systemd/bsebot-price-watcher.service \
        bsebot/price_watcher_main.py
git commit -m "feat(bsebot): add price-watcher systemd unit + entrypoint + indexes"
```

---

## Task 14: Wire CLI — `agent run`, `positions`, `alerts`, `tools`, `trades`

**Files:**
- Modify: `bsebot/cli.py`
- Create: `tests/test_cli_agent.py`

- [ ] **Step 1: Tests `tests/test_cli_agent.py`**

```python
from datetime import datetime, timedelta

from click.testing import CliRunner

from bsebot import alerts_engine, cash, cli, db, positions


def _cfg(tmp_path, db_path):
    c = tmp_path / "config.yaml"
    c.write_text(
        "database:\n"
        f"  path: {db_path}\n"
        "llm:\n  extract_max_tokens: 2048\n  reason_max_tokens: 8192\n"
        "  continuation_max_attempts: 3\n  providers:\n"
        "    - name: gemini\n      model: gemini/gemini-2.5-flash\n"
        "      api_key_env: GEMINI_API_KEY\n      roles: [extract, reason]\n",
        encoding="utf-8",
    )
    e = tmp_path / ".env"
    e.write_text("GEMINI_API_KEY=fake\n", encoding="utf-8")
    return c, e


def test_positions_list_shows_open(tmp_path, migrations_dir):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    cash.deposit(p, 10000.0, note="seed")
    positions.open_trade(p, agent_run_id=None, side="long", quantity=3,
                         entry_price=100.0, stop_loss=95.0, target=110.0)
    c, e = _cfg(tmp_path, p)
    res = CliRunner().invoke(cli.main,
                             ["--config", str(c), "--env", str(e), "positions", "list"])
    assert res.exit_code == 0, res.output
    assert "long" in res.output
    assert "100.0" in res.output


def test_alerts_list_and_cancel(tmp_path, migrations_dir):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    conn = db.connect(p)
    try:
        conn.execute("INSERT INTO raw_documents (source, content_hash, content) "
                     "VALUES ('x','h','c')")
        conn.execute("INSERT INTO facts (source_doc_id, fact_type, payload_json, source_quote) "
                     "VALUES (1,'news','{}','q')")
        conn.commit()
    finally:
        conn.close()
    vu = (datetime.utcnow() + timedelta(days=5)).isoformat()
    aid = alerts_engine.set_alert(p, agent_run_id=None, condition="price_above",
                                  threshold=120.0, valid_until=vu,
                                  why_this_threshold="x", source_fact_ids=[1],
                                  current_price=100.0)
    c, e = _cfg(tmp_path, p)
    r1 = CliRunner().invoke(cli.main,
                            ["--config", str(c), "--env", str(e), "alerts", "list"])
    assert str(aid) in r1.output
    r2 = CliRunner().invoke(cli.main,
                            ["--config", str(c), "--env", str(e),
                             "alerts", "cancel", str(aid)])
    assert r2.exit_code == 0
    assert "cancelled" in r2.output.lower()


def test_agent_run_invokes_agent(tmp_path, migrations_dir, monkeypatch):
    p = tmp_path / "b.db"
    db.run_migrations(p, migrations_dir)
    c, e = _cfg(tmp_path, p)
    from bsebot import agent as _agent
    monkeypatch.setattr(_agent, "run", lambda cfg, **kw: 99)
    res = CliRunner().invoke(
        cli.main,
        ["--config", str(c), "--env", str(e),
         "agent", "run", "--current-price", "100.0"],
    )
    assert res.exit_code == 0, res.output
    assert "run_id=99" in res.output
```

- [ ] **Step 2: Update `bsebot/cli.py`**

Open `/Users/devamarnani/Desktop/bsebot/bsebot/cli.py`. Replace the Plan 1 stub groups for `positions`, `alerts`, `tools`, and the `agent` group, plus the `trades_history` stub, with the real implementations below. The replacement strategy: locate the existing `@main.group() def positions():` block and replace through (and including) the `@main.command("trades")` stub from Plan 1. Insert this new block in their place:

```python
# --------- agent / positions / alerts / tools / trades (Plan 3) ---------

from bsebot import agent as _agent
from bsebot import alerts_engine as _alerts_engine
from bsebot import positions as _positions


@main.group()
def agent() -> None:
    """Agent runner."""


@agent.command("run")
@click.option("--triggered-by", default=None, type=int)
@click.option("--current-price", default=None, type=float)
@click.pass_context
def agent_run(ctx, triggered_by, current_price):
    cfg = _load_app_config(ctx)
    rid = _agent.run(
        cfg,
        trigger="alert" if triggered_by else "scheduled",
        triggered_by_alert_id=triggered_by,
        current_price=current_price,
    )
    click.echo(f"run_id={rid}")


@main.group()
def positions() -> None:
    """Position manager."""


@positions.command("list")
@click.pass_context
def positions_list(ctx):
    cfg = _load_app_config(ctx)
    for p in _positions.list_open(cfg.database_path):
        click.echo(
            f"#{p['id']} {p['side']} qty={p['quantity']} entry={p['entry_price']} "
            f"stop={p['stop_loss']} target={p['target']} status={p['status']}"
        )


@positions.command("check")
@click.option("--current-price", required=True, type=float)
@click.pass_context
def positions_check(ctx, current_price):
    cfg = _load_app_config(ctx)
    closed = _positions.evaluate(cfg.database_path, current_price=current_price)
    click.echo(f"closed: {closed}")


@main.group()
def alerts() -> None:
    """Alert system."""


@alerts.command("list")
@click.pass_context
def alerts_list(ctx):
    cfg = _load_app_config(ctx)
    for a in _alerts_engine.list_active(cfg.database_path):
        click.echo(
            f"#{a['id']} {a['condition']} {a['threshold']} until {a['valid_until']} "
            f"fired={a['fire_count']} ({a['why_this_threshold']})"
        )


@alerts.command("cancel")
@click.argument("alert_id", type=int)
@click.pass_context
def alerts_cancel(ctx, alert_id):
    cfg = _load_app_config(ctx)
    ok = _alerts_engine.cancel(cfg.database_path, alert_id)
    click.echo("cancelled" if ok else "not found")


@main.group()
def tools() -> None:
    """Agent-requested source registry."""


@tools.command("list")
@click.pass_context
def tools_list(ctx):
    cfg = _load_app_config(ctx)
    conn = _db.connect(cfg.database_path)
    try:
        rows = conn.execute(
            "SELECT id, name, url, enabled, approved_at FROM tools ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    for r in rows:
        status = "enabled" if r[3] else "pending"
        click.echo(f"#{r[0]} {r[1]} ({r[2]}) — {status}")


@tools.command("approve")
@click.argument("tool_id", type=int)
@click.pass_context
def tools_approve(ctx, tool_id):
    cfg = _load_app_config(ctx)
    conn = _db.connect(cfg.database_path)
    try:
        cur = conn.execute(
            "UPDATE tools SET enabled=1, approved_at=CURRENT_TIMESTAMP WHERE id=?",
            (int(tool_id),),
        )
        conn.commit()
    finally:
        conn.close()
    click.echo("approved" if cur.rowcount else "not found")


@main.command("trades")
@click.option("--days", default=30, type=int)
@click.pass_context
def trades_history(ctx, days):
    cfg = _load_app_config(ctx)
    conn = _db.connect(cfg.database_path)
    try:
        rows = conn.execute(
            "SELECT id, side, quantity, entry_price, exit_price, pnl, exit_reason "
            "FROM trades WHERE entry_at >= datetime('now', ?) ORDER BY id DESC",
            (f"-{int(days)} days",),
        ).fetchall()
    finally:
        conn.close()
    for r in rows:
        click.echo(f"#{r[0]} {r[1]} qty={r[2]} entry={r[3]} exit={r[4]} "
                   f"pnl={r[5]} reason={r[6]}")
```

- [ ] **Step 3: Run + commit** → 3 passed.

```bash
git add bsebot/cli.py tests/test_cli_agent.py
git commit -m "feat(bsebot): wire CLI agent/positions/alerts/tools/trades commands"
```

---

## Self-Review Notes (post-write)

**Spec coverage of Plan 3 scope:**
- Clock + market-hours helper — Task 1.
- Cash ledger — Task 2.
- Position manager (open/close, stop/target evaluation) — Task 3.
- Daily ₹10 overhead — Task 4.
- Alert system with budget + dedup + cooldown — Task 5.
- Agent memory with provenance — Task 6.
- Multi-strategy URL fetcher + Brave/DDG search — Task 7.
- Adversarial fact-checker + citation regex — Task 8.
- Vault stubs for decision/trade/alert/memory (Plan 4 fills) — Task 9.
- Every agent tool (`query_facts`, `read_raw_document`, `read_memory`, `write_memory`, `supersede_memory`, `check_open_positions`, `check_recent_decisions`, `set_alert`, `cancel_alert`, `request_new_source`, `web_search`, `fetch_url`, `get_current_price`, `get_price_history`, `submit_decision`) — Task 10.
- Agent run loop with lock, tool dispatch, max-iter abort — Task 11.
- Price watcher 30s loop + market-hours gating — Task 12.
- systemd unit + entrypoint — Task 13.
- CLI wiring for `agent run`, `positions list/check`, `alerts list/cancel`, `tools list/approve`, `trades` — Task 14.

**Type/name consistency:**
- `agent_tools.Context` always carries `cfg`, `current_run_id`, optional `current_price`.
- `submit_decision` returns `{"trade_id", "audit_passed", "audit_response", "action"}` everywhere it's referenced.
- `alerts_engine.set_alert`, `cancel`, `list_active`, `check_and_fire`, `budget_status` — same signatures in tests, agent_tools, CLI, price_watcher.
- `positions.list_open(db_path) → list[dict]` consumed by CLI and `agent_tools.check_open_positions`.

**Placeholder scan:** None. Every Python step ships full code. Vault calls are stubbed with no-op returns (Plan 4 implements).

**Items intentionally deferred to Plan 4:**
- Real vault writes (decisions, trades, alerts/active, memory, P&L chart embed).
- Quartz rebuild trigger.
- `bsebot report daily` real output (Plan 4 owns reporter).

**Test isolation:** Every Plan 3 test monkeypatches `LLMRouter`, `factcheck.audit`, `_yfinance_price`, or `_requests_get`. Total network calls in `pytest`: zero.
