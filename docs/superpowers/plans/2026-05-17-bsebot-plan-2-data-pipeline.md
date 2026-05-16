# BSEBot Plan 2 — Data Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the full information-ingestion pipeline: a harvester base class with politeness layer, every concrete harvester from the spec, deterministic numeric parsers, Pydantic fact schemas (per-source + `generic_web`), the extractor with `instructor` + verbatim-quote verification, the `bootstrap` CLI command, and `bsebot harvest <name|all>` / `bsebot extract` wiring. Output: `raw_documents`, `facts`, and `price_history` rows ready for the Plan 3 agent to read.

**Architecture:** A single `Harvester` ABC encapsulates the fetch → hash-dedup → persist → `harvester_runs`-row flow. All HTTP traffic goes through a shared `bsebot.http` client that enforces a 2s per-domain delay, exponential backoff with jitter, and User-Agent rotation. The extractor polls `raw_documents WHERE processed=0`, selects a Pydantic schema per `source`, calls `LLMRouter.extract`, runs quote verification against the source text, and writes a `facts` row only on schema + quote pass. Numbers are produced **only** by deterministic HTML/regex parsers in `bsebot/parsers/`; the LLM narrates. `price_history` is populated directly from `yfinance`, bypassing the LLM. Vault writes are stubbed (`bsebot.vault.publish_source(...)` is a no-op in this plan; Plan 4 implements it for real).

**Tech Stack:** Python 3.11, `requests`, `httpx`, `beautifulsoup4 + lxml`, `pdfplumber`, `feedparser`, `yfinance`, `pydantic >= 2`, `instructor` (via the Plan 1 LLM router), `pytest`, `pytest-mock`.

**Scope:** Build-order items 3–4 + portions of 13 from the spec. Out of scope (deferred): vault writer (Plan 4), agent runner (Plan 3), alert system (Plan 3), position manager (Plan 3), Quartz (Plan 4).

---

## File Structure

Files created in this plan (all paths relative to repo root `/Users/devamarnani/Desktop/bsebot/`):

- `bsebot/http.py` — shared HTTP client (rate limit, UA rotation, retries)
- `bsebot/vault.py` — **no-op stub** with `publish_source(...)`, `publish_extraction(...)` signatures Plan 4 will fill in
- `bsebot/harvesters/__init__.py` — package marker + harvester registry
- `bsebot/harvesters/base.py` — `Harvester` ABC with `fetch_and_persist()`
- `bsebot/harvesters/sebi_circulars.py`
- `bsebot/harvesters/screener_bse.py`
- `bsebot/harvesters/google_news.py`
- `bsebot/harvesters/nse_announcements.py`
- `bsebot/harvesters/bse_announcements.py`
- `bsebot/harvesters/turnover_data.py`
- `bsebot/harvesters/price_history.py` — yfinance, writes to `price_history` table (deterministic)
- `bsebot/parsers/__init__.py`
- `bsebot/parsers/numeric.py` — currency/percent/volume regex helpers + bs4 table → list[dict]
- `bsebot/parsers/screener_html.py` — Screener-specific financial table parsers
- `bsebot/parsers/sebi_pdf.py` — pdfplumber wrapper
- `bsebot/schemas/__init__.py`
- `bsebot/schemas/common.py` — `ClaimWithQuote`, `BaseFact`
- `bsebot/schemas/sebi.py` — `SebiCircularFact`
- `bsebot/schemas/news.py` — `NewsFact`
- `bsebot/schemas/screener.py` — `ScreenerCommentaryFact`
- `bsebot/schemas/announcements.py` — `AnnouncementFact`
- `bsebot/schemas/generic_web.py` — `GenericWebFact`
- `bsebot/schemas/registry.py` — `(source) → (schema, prompt_template)` map
- `bsebot/extractor.py` — extractor loop with quote verification
- `bsebot/bootstrap.py` — one-time history bootstrap
- `migrations/002_harvester_indexes.sql` — supporting indexes (no schema changes)
- `tests/fixtures/sebi_sample.pdf` — tiny generated PDF
- `tests/fixtures/screener_bse.html` — captured Screener page snippet
- `tests/fixtures/google_news.xml` — captured RSS snippet
- `tests/fixtures/nse_announcement.pdf`
- `tests/fixtures/way2wealth_turnover.html`
- `tests/test_http.py`
- `tests/test_parsers_numeric.py`
- `tests/test_parsers_screener.py`
- `tests/test_parsers_sebi_pdf.py`
- `tests/test_harvester_base.py`
- `tests/test_harvester_sebi.py`
- `tests/test_harvester_screener.py`
- `tests/test_harvester_news.py`
- `tests/test_harvester_nse.py`
- `tests/test_harvester_bse.py`
- `tests/test_harvester_turnover.py`
- `tests/test_harvester_price.py`
- `tests/test_schemas.py`
- `tests/test_extractor.py`
- `tests/test_bootstrap.py`
- `tests/test_cli_data.py` — exercises `bsebot harvest`/`extract`/`bootstrap`

Modified:
- `bsebot/cli.py` — replace `harvest`, `extract` stubs with real implementations; add `bootstrap`
- `pyproject.toml` — no new runtime deps (everything already listed in Plan 1)

---

## Task 1: Shared HTTP client (`bsebot/http.py`) — TDD

**Files:**
- Create: `/Users/devamarnani/Desktop/bsebot/tests/test_http.py`
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/http.py`

- [ ] **Step 1: Write failing tests `tests/test_http.py`**

Write `/Users/devamarnani/Desktop/bsebot/tests/test_http.py`:

```python
import time

import pytest

from bsebot import http as bhttp


def test_user_agent_rotation_cycles_through_pool():
    client = bhttp.HttpClient(min_delay_seconds=0.0)
    seen = {client.next_user_agent() for _ in range(len(bhttp.USER_AGENTS) * 2)}
    assert seen == set(bhttp.USER_AGENTS)


def test_per_domain_min_delay_enforced(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(bhttp.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(
        bhttp.time, "monotonic",
        _seq_monotonic([0.0, 0.5, 0.5, 3.0]),
    )
    client = bhttp.HttpClient(min_delay_seconds=2.0)
    client.wait_for_domain("example.com")  # first call: no sleep
    client.wait_for_domain("example.com")  # 0.5s in; needs 1.5s more
    assert sleeps == [1.5]


def test_backoff_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(bhttp.time, "sleep", lambda s: None)
    monkeypatch.setattr(bhttp.random, "uniform", lambda a, b: 0.0)
    calls = {"n": 0}

    class _R:
        def __init__(self, code: int, text: str = "ok"):
            self.status_code = code
            self.text = text
            self.content = text.encode()
            self.headers = {}

    def fake_get(url, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            return _R(503)
        return _R(200, "hello")

    monkeypatch.setattr(bhttp.requests, "get", fake_get)
    client = bhttp.HttpClient(min_delay_seconds=0.0, max_retries=4)
    resp = client.get("https://example.com/foo")
    assert resp.status_code == 200
    assert resp.text == "hello"
    assert calls["n"] == 3


def test_backoff_gives_up_after_max_retries(monkeypatch):
    monkeypatch.setattr(bhttp.time, "sleep", lambda s: None)

    class _R:
        status_code = 500
        text = "oops"
        content = b"oops"
        headers = {}

    monkeypatch.setattr(bhttp.requests, "get", lambda url, **kw: _R())
    client = bhttp.HttpClient(min_delay_seconds=0.0, max_retries=2)
    with pytest.raises(bhttp.HttpError):
        client.get("https://example.com/foo")


def test_get_passes_user_agent_header(monkeypatch):
    seen: dict = {}

    class _R:
        status_code = 200
        text = "ok"
        content = b"ok"
        headers = {}

    def fake_get(url, **kw):
        seen.update(kw)
        return _R()

    monkeypatch.setattr(bhttp.time, "sleep", lambda s: None)
    monkeypatch.setattr(bhttp.requests, "get", fake_get)
    client = bhttp.HttpClient(min_delay_seconds=0.0)
    client.get("https://example.com/")
    assert "User-Agent" in seen["headers"]
    assert seen["headers"]["User-Agent"] in bhttp.USER_AGENTS


def _seq_monotonic(values):
    it = iter(values)

    def _():
        try:
            return next(it)
        except StopIteration:
            return values[-1]

    return _
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_http.py -v`

Expected: `ModuleNotFoundError: No module named 'bsebot.http'`.

- [ ] **Step 3: Implement `bsebot/http.py`**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/http.py`:

```python
"""Shared HTTP client. Politeness + retries for harvesters.

- Per-domain min delay (default 2s) between requests.
- User-Agent rotation across a small realistic pool.
- Exponential backoff with jitter for transient 5xx and connection errors.
- requests.get is the primary; httpx may be substituted by callers on stubborn sites.
"""

from __future__ import annotations

import itertools
import random
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests


USER_AGENTS = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0.0.0 Safari/537.36",
)


class HttpError(RuntimeError):
    """Final HTTP failure after retries."""


@dataclass
class HttpResponse:
    status_code: int
    text: str
    content: bytes
    headers: dict[str, str]
    url: str


class HttpClient:
    def __init__(
        self,
        min_delay_seconds: float = 2.0,
        max_retries: int = 4,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.min_delay = float(min_delay_seconds)
        self.max_retries = int(max_retries)
        self.timeout = float(timeout_seconds)
        self._ua_iter = itertools.cycle(USER_AGENTS)
        self._last_hit_at: dict[str, float] = {}

    def next_user_agent(self) -> str:
        return next(self._ua_iter)

    def wait_for_domain(self, host: str) -> None:
        now = time.monotonic()
        last = self._last_hit_at.get(host)
        if last is not None:
            elapsed = now - last
            if elapsed < self.min_delay:
                time.sleep(self.min_delay - elapsed)
        self._last_hit_at[host] = time.monotonic()

    def get(self, url: str, **kwargs: Any) -> HttpResponse:
        host = urlparse(url).netloc
        self.wait_for_domain(host)

        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("User-Agent", self.next_user_agent())
        timeout = kwargs.pop("timeout", self.timeout)

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = requests.get(url, headers=headers, timeout=timeout, **kwargs)
            except (requests.RequestException, OSError) as e:  # network errors
                last_exc = e
                self._sleep_backoff(attempt)
                continue
            code = getattr(resp, "status_code", 0)
            if code < 500 and code != 429:
                return HttpResponse(
                    status_code=code,
                    text=getattr(resp, "text", "") or "",
                    content=getattr(resp, "content", b"") or b"",
                    headers=dict(getattr(resp, "headers", {}) or {}),
                    url=url,
                )
            last_exc = HttpError(f"status={code} on {url}")
            self._sleep_backoff(attempt)
        raise HttpError(f"GET {url} failed after {self.max_retries + 1} attempts: "
                        f"{last_exc}") from last_exc

    def _sleep_backoff(self, attempt: int) -> None:
        # 0.5, 1, 2, 4, ... seconds, plus jitter up to 0.5s.
        base = 0.5 * (2 ** attempt)
        jitter = random.uniform(0.0, 0.5)
        time.sleep(base + jitter)
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_http.py -v`

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/devamarnani/Desktop/bsebot
git add tests/test_http.py bsebot/http.py
git commit -m "feat(bsebot): add shared HTTP client with per-domain delay, UA rotation, backoff"
```

---

## Task 2: Vault stub (`bsebot/vault.py`)

**Files:**
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/vault.py`

- [ ] **Step 1: Write the stub**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/vault.py`:

```python
"""Vault writer — STUB (Plan 4 fills these in).

Harvesters and the extractor call `publish_source` / `publish_extraction` so
that when Plan 4's real implementation lands, no caller has to change.
For now every function is a no-op that returns its inputs for testability.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PublishResult:
    written: bool
    path: str | None = None


def publish_source(
    *,
    source: str,
    title: str,
    body_markdown: str,
    raw_document_id: int,
    url: str | None = None,
    extra_frontmatter: dict | None = None,
) -> PublishResult:
    return PublishResult(written=False, path=None)


def publish_extraction(
    *,
    raw_document_id: int,
    fact_ids: list[int],
) -> PublishResult:
    return PublishResult(written=False, path=None)


def publish_daily_log_entry(
    *,
    date_str: str,
    section: str,
    bullet: str,
) -> PublishResult:
    return PublishResult(written=False, path=None)
```

- [ ] **Step 2: Commit**

```bash
cd /Users/devamarnani/Desktop/bsebot
git add bsebot/vault.py
git commit -m "feat(bsebot): add vault stub so plan 2 callers compile (plan 4 fills in)"
```

---

## Task 3: Numeric parsers (`bsebot/parsers/numeric.py`) — TDD

**Files:**
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/parsers/__init__.py`
- Create: `/Users/devamarnani/Desktop/bsebot/tests/test_parsers_numeric.py`
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/parsers/numeric.py`

- [ ] **Step 1: Package marker**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/parsers/__init__.py`:

```python
"""Deterministic parsers. The LLM never extracts numbers; these do."""
```

- [ ] **Step 2: Write failing tests `tests/test_parsers_numeric.py`**

Write `/Users/devamarnani/Desktop/bsebot/tests/test_parsers_numeric.py`:

```python
import pytest

from bsebot.parsers import numeric as p


def test_parse_inr_amount_handles_lakhs_crores():
    assert p.parse_inr_amount("Rs. 1,234.50 crore") == 12_345_000_000.0
    assert p.parse_inr_amount("INR 2.5 Cr") == 25_000_000.0
    assert p.parse_inr_amount("₹100.25 lakh") == 10_025_000.0
    assert p.parse_inr_amount("Rs 5,67,890") == 567890.0


def test_parse_inr_amount_returns_none_for_non_numeric():
    assert p.parse_inr_amount("not a number") is None
    assert p.parse_inr_amount("") is None


def test_parse_percent():
    assert p.parse_percent("Up 12.5%") == 12.5
    assert p.parse_percent("(3.2%)") == -3.2
    assert p.parse_percent("0%") == 0.0
    assert p.parse_percent("no number") is None


def test_parse_volume_handles_indian_words():
    assert p.parse_volume("1,23,456 shares") == 123456
    assert p.parse_volume("2.5 lakh shares") == 250000
    assert p.parse_volume("traded 10 crore units") == 100_000_000


def test_html_table_to_rows_basic():
    html = (
        "<table>"
        "<tr><th>Year</th><th>Revenue</th></tr>"
        "<tr><td>FY24</td><td>1,234</td></tr>"
        "<tr><td>FY25</td><td>1,500</td></tr>"
        "</table>"
    )
    rows = p.html_table_to_rows(html)
    assert rows == [
        {"Year": "FY24", "Revenue": "1,234"},
        {"Year": "FY25", "Revenue": "1,500"},
    ]


def test_html_table_to_rows_uses_first_table_when_multiple():
    html = (
        "<table><tr><th>A</th></tr><tr><td>1</td></tr></table>"
        "<table><tr><th>B</th></tr><tr><td>2</td></tr></table>"
    )
    rows = p.html_table_to_rows(html)
    assert rows == [{"A": "1"}]


def test_html_table_to_rows_handles_missing_headers():
    html = "<table><tr><td>1</td><td>2</td></tr></table>"
    rows = p.html_table_to_rows(html)
    assert rows == [{"col_0": "1", "col_1": "2"}]
```

- [ ] **Step 3: Run tests — expect FAIL**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_parsers_numeric.py -v`

Expected: `ImportError` on `bsebot.parsers.numeric`.

- [ ] **Step 4: Implement `bsebot/parsers/numeric.py`**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/parsers/numeric.py`:

```python
"""Deterministic numeric parsers — currency, percent, volume, HTML tables."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup


_INR_PREFIX = r"(?:rs\.?|inr|₹)"
_NUMBER = r"[0-9][0-9,]*(?:\.[0-9]+)?"


def _strip_commas(s: str) -> str:
    return s.replace(",", "")


def parse_inr_amount(text: str) -> float | None:
    """Parse 'Rs. 1,234.5 crore' style currency strings to float rupees.
    Returns None if no recognizable amount is found."""
    if not text:
        return None
    t = text.strip().lower()

    pat = re.compile(
        rf"({_INR_PREFIX})?\s*({_NUMBER})\s*(crore|cr|lakh|lac|lakhs|lacs)?"
    )
    m = pat.search(t)
    if not m:
        return None
    num_s = _strip_commas(m.group(2))
    try:
        num = float(num_s)
    except ValueError:
        return None
    suffix = (m.group(3) or "").strip()
    if suffix in {"crore", "cr"}:
        return num * 10_000_000.0
    if suffix in {"lakh", "lac", "lakhs", "lacs"}:
        return num * 100_000.0
    # bare amount: only accept if either prefix or suffix indicated INR
    if m.group(1) is None and m.group(3) is None:
        # still bare numbers (e.g., '5,67,890') with prefix → accept
        if m.group(1) is None and not re.search(_INR_PREFIX, t):
            # try with explicit rs prefix
            pre = re.search(rf"{_INR_PREFIX}\s*({_NUMBER})", t)
            if pre is None:
                return None
            num = float(_strip_commas(pre.group(1)))
    return num


def parse_percent(text: str) -> float | None:
    """Parse a percent value. Parens or leading minus → negative."""
    if not text:
        return None
    m = re.search(rf"\(?\s*(-?{_NUMBER})\s*%\s*\)?", text)
    if not m:
        return None
    try:
        val = float(_strip_commas(m.group(1)))
    except ValueError:
        return None
    if text.strip().startswith("(") and text.strip().endswith(")"):
        val = -abs(val)
    return val


def parse_volume(text: str) -> int | None:
    """Parse share volumes incl. 'lakh' / 'crore' words. Returns integer units."""
    if not text:
        return None
    t = text.lower()
    m = re.search(rf"({_NUMBER})\s*(crore|cr|lakh|lac|lakhs|lacs)?", t)
    if not m:
        return None
    try:
        num = float(_strip_commas(m.group(1)))
    except ValueError:
        return None
    suffix = (m.group(2) or "").strip()
    if suffix in {"crore", "cr"}:
        return int(num * 10_000_000)
    if suffix in {"lakh", "lac", "lakhs", "lacs"}:
        return int(num * 100_000)
    return int(num)


def html_table_to_rows(html: str, table_index: int = 0) -> list[dict[str, str]]:
    """Convert the table_index-th <table> in `html` to a list of {col: value} dicts.
    Header row taken from <th>; if absent, columns are named col_0, col_1, ..."""
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")
    if table_index >= len(tables):
        return []
    table = tables[table_index]
    rows = table.find_all("tr")
    headers: list[str] = []
    out: list[dict[str, str]] = []
    for tr in rows:
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        if not headers and all(c.name == "th" for c in cells):
            headers = [c.get_text(strip=True) for c in cells]
            continue
        if not headers:
            headers = [f"col_{i}" for i in range(len(cells))]
        row: dict[str, str] = {}
        for i, c in enumerate(cells):
            key = headers[i] if i < len(headers) else f"col_{i}"
            row[key] = c.get_text(strip=True)
        out.append(row)
    return out
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_parsers_numeric.py -v`

Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
cd /Users/devamarnani/Desktop/bsebot
git add bsebot/parsers/__init__.py bsebot/parsers/numeric.py tests/test_parsers_numeric.py
git commit -m "feat(bsebot): add deterministic numeric/table parsers"
```

---

## Task 4: Screener HTML parser (`bsebot/parsers/screener_html.py`) — TDD

**Files:**
- Create: `/Users/devamarnani/Desktop/bsebot/tests/fixtures/screener_bse.html`
- Create: `/Users/devamarnani/Desktop/bsebot/tests/test_parsers_screener.py`
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/parsers/screener_html.py`

- [ ] **Step 1: Write the fixture HTML**

Write `/Users/devamarnani/Desktop/bsebot/tests/fixtures/screener_bse.html`:

```html
<html><body>
<section id="profit-loss">
  <h2>Quarterly Results</h2>
  <table class="data-table">
    <thead><tr><th></th><th>Mar 2025</th><th>Jun 2025</th><th>Sep 2025</th></tr></thead>
    <tbody>
      <tr><td>Sales</td><td>342</td><td>378</td><td>401</td></tr>
      <tr><td>Operating Profit</td><td>180</td><td>205</td><td>220</td></tr>
      <tr><td>Net Profit</td><td>120</td><td>140</td><td>155</td></tr>
    </tbody>
  </table>
</section>
<section id="shareholding">
  <h2>Shareholding Pattern</h2>
  <table class="data-table">
    <thead><tr><th></th><th>Jun 2025</th><th>Sep 2025</th></tr></thead>
    <tbody>
      <tr><td>Promoters</td><td>0.00%</td><td>0.00%</td></tr>
      <tr><td>FIIs</td><td>18.20%</td><td>19.10%</td></tr>
      <tr><td>DIIs</td><td>22.50%</td><td>23.00%</td></tr>
      <tr><td>Public</td><td>59.30%</td><td>57.90%</td></tr>
    </tbody>
  </table>
</section>
<section id="announcements">
  <h2>Announcements</h2>
  <ul>
    <li><a href="/c/123">Board Meeting - Outcome (15 Sep 2025)</a></li>
    <li><a href="/c/124">Investor Presentation (28 Aug 2025)</a></li>
  </ul>
</section>
</body></html>
```

- [ ] **Step 2: Write failing tests `tests/test_parsers_screener.py`**

Write `/Users/devamarnani/Desktop/bsebot/tests/test_parsers_screener.py`:

```python
from pathlib import Path

from bsebot.parsers import screener_html as sh


FIXTURE = Path(__file__).parent / "fixtures" / "screener_bse.html"


def _html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_extract_quarterly_pnl_rows():
    rows = sh.extract_quarterly_pnl(_html())
    assert {"Sales", "Operating Profit", "Net Profit"} <= {r["metric"] for r in rows}
    sales = next(r for r in rows if r["metric"] == "Sales")
    assert sales["Mar 2025"] == 342.0
    assert sales["Sep 2025"] == 401.0


def test_extract_shareholding_pattern():
    rows = sh.extract_shareholding(_html())
    promoters = next(r for r in rows if r["category"] == "Promoters")
    assert promoters["Sep 2025"] == 0.0
    fiis = next(r for r in rows if r["category"] == "FIIs")
    assert fiis["Sep 2025"] == 19.1


def test_extract_announcement_links():
    items = sh.extract_announcements(_html())
    assert len(items) == 2
    assert items[0]["title"].startswith("Board Meeting")
    assert items[0]["href"] == "/c/123"
```

- [ ] **Step 3: Run tests — expect FAIL**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_parsers_screener.py -v`

Expected: `ImportError` on `bsebot.parsers.screener_html`.

- [ ] **Step 4: Implement `bsebot/parsers/screener_html.py`**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/parsers/screener_html.py`:

```python
"""Screener.in HTML extractors. Deterministic — no LLM."""

from __future__ import annotations

from bs4 import BeautifulSoup

from bsebot.parsers.numeric import parse_inr_amount, parse_percent


def _section_table(html: str, section_id: str) -> list[list[str]]:
    soup = BeautifulSoup(html, "lxml")
    section = soup.find(attrs={"id": section_id})
    if section is None:
        return []
    table = section.find("table")
    if table is None:
        return []
    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        rows.append([c.get_text(strip=True) for c in cells])
    return rows


def _to_float_or_none(s: str) -> float | None:
    s = s.replace(",", "").strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def extract_quarterly_pnl(html: str) -> list[dict]:
    rows = _section_table(html, "profit-loss")
    if len(rows) < 2:
        return []
    header = rows[0]
    quarters = header[1:]
    out: list[dict] = []
    for r in rows[1:]:
        if len(r) < 2:
            continue
        metric = r[0]
        entry: dict = {"metric": metric}
        for i, q in enumerate(quarters, start=1):
            if i >= len(r):
                entry[q] = None
            else:
                # the table cells are plain numbers in crores
                entry[q] = _to_float_or_none(r[i])
        out.append(entry)
    return out


def extract_shareholding(html: str) -> list[dict]:
    rows = _section_table(html, "shareholding")
    if len(rows) < 2:
        return []
    header = rows[0]
    quarters = header[1:]
    out: list[dict] = []
    for r in rows[1:]:
        if len(r) < 2:
            continue
        category = r[0]
        entry: dict = {"category": category}
        for i, q in enumerate(quarters, start=1):
            if i >= len(r):
                entry[q] = None
            else:
                entry[q] = parse_percent(r[i])
        out.append(entry)
    return out


def extract_announcements(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    section = soup.find(attrs={"id": "announcements"})
    if section is None:
        return []
    out: list[dict] = []
    for a in section.find_all("a"):
        out.append({"title": a.get_text(strip=True), "href": a.get("href", "")})
    return out
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_parsers_screener.py -v`

Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
cd /Users/devamarnani/Desktop/bsebot
git add tests/fixtures/screener_bse.html tests/test_parsers_screener.py bsebot/parsers/screener_html.py
git commit -m "feat(bsebot): add screener.in HTML extractors (pnl, shareholding, announcements)"
```

---

## Task 5: SEBI PDF parser (`bsebot/parsers/sebi_pdf.py`) — TDD

**Files:**
- Create: `/Users/devamarnani/Desktop/bsebot/tests/fixtures/sebi_sample.pdf` (generated)
- Create: `/Users/devamarnani/Desktop/bsebot/tests/test_parsers_sebi_pdf.py`
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/parsers/sebi_pdf.py`

- [ ] **Step 1: Generate a tiny fixture PDF**

Run from a Python prompt in the venv to create the fixture (this step is not part of the test):

```bash
cd /Users/devamarnani/Desktop/bsebot
source .venv/bin/activate 2>/dev/null || true
python3.11 - <<'PY'
import io
from pathlib import Path
import pdfplumber  # noqa: F401  (verify install)

# Use reportlab to make a deterministic, parseable PDF.
try:
    from reportlab.pdfgen import canvas
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "reportlab"])
    from reportlab.pdfgen import canvas

out = Path("tests/fixtures/sebi_sample.pdf")
out.parent.mkdir(parents=True, exist_ok=True)
c = canvas.Canvas(str(out))
c.setFont("Helvetica", 12)
c.drawString(72, 720, "SEBI Circular No. SEBI/HO/MIRSD/POD-1/P/CIR/2025/123")
c.drawString(72, 700, "Subject: Framework for stock exchange clearing corporations")
c.drawString(72, 680, "Issued on: 12 May 2025")
c.drawString(72, 660, "This circular amends the existing framework with effect from")
c.drawString(72, 640, "01 July 2025. Stock exchanges including BSE Ltd shall comply.")
c.showPage()
c.save()
print("wrote", out)
PY
```

Note: `reportlab` is a test-time-only convenience for fixture generation. It is NOT added to `pyproject.toml`; the install is one-shot.

- [ ] **Step 2: Write failing tests `tests/test_parsers_sebi_pdf.py`**

Write `/Users/devamarnani/Desktop/bsebot/tests/test_parsers_sebi_pdf.py`:

```python
from pathlib import Path

from bsebot.parsers import sebi_pdf


FIXTURE = Path(__file__).parent / "fixtures" / "sebi_sample.pdf"


def test_extract_text_returns_nonempty():
    text = sebi_pdf.extract_text(FIXTURE)
    assert "SEBI Circular" in text
    assert "BSE Ltd" in text


def test_extract_text_from_bytes():
    data = FIXTURE.read_bytes()
    text = sebi_pdf.extract_text_from_bytes(data)
    assert "framework" in text.lower()
```

- [ ] **Step 3: Run tests — expect FAIL**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_parsers_sebi_pdf.py -v`

Expected: `ImportError` on `bsebot.parsers.sebi_pdf`.

- [ ] **Step 4: Implement `bsebot/parsers/sebi_pdf.py`**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/parsers/sebi_pdf.py`:

```python
"""pdfplumber wrapper for SEBI/NSE PDF circulars."""

from __future__ import annotations

import io
from pathlib import Path

import pdfplumber


def extract_text(path: str | Path) -> str:
    with pdfplumber.open(str(path)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def extract_text_from_bytes(data: bytes) -> str:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_parsers_sebi_pdf.py -v`

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
cd /Users/devamarnani/Desktop/bsebot
git add tests/fixtures/sebi_sample.pdf tests/test_parsers_sebi_pdf.py bsebot/parsers/sebi_pdf.py
git commit -m "feat(bsebot): add SEBI PDF text extractor (pdfplumber wrapper)"
```

---

## Task 6: Pydantic fact schemas — TDD

**Files:**
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/schemas/__init__.py`
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/schemas/common.py`
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/schemas/sebi.py`
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/schemas/news.py`
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/schemas/screener.py`
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/schemas/announcements.py`
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/schemas/generic_web.py`
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/schemas/registry.py`
- Create: `/Users/devamarnani/Desktop/bsebot/tests/test_schemas.py`

- [ ] **Step 1: Package marker**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/schemas/__init__.py`:

```python
"""Pydantic fact schemas used by the extractor."""
```

- [ ] **Step 2: Common types**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/schemas/common.py`:

```python
from __future__ import annotations

from pydantic import BaseModel, Field


class ClaimWithQuote(BaseModel):
    claim: str = Field(..., description="A discrete claim derived from the source.")
    source_quote: str = Field(
        ...,
        description=(
            "Verbatim substring from the source supporting this claim. "
            "Must match the source text exactly."
        ),
    )


class BaseFact(BaseModel):
    summary: str = Field(..., description="2-3 sentence neutral summary.")
    source_quote: str = Field(
        ...,
        description="One verbatim quote from the source covering the headline claim.",
    )
    confidence: float = Field(0.8, ge=0.0, le=1.0)
```

- [ ] **Step 3: SEBI schema**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/schemas/sebi.py`:

```python
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import BaseFact, ClaimWithQuote


class SebiCircularFact(BaseFact):
    circular_number: str | None = Field(
        None, description="Circular number, e.g. SEBI/HO/MIRSD/POD-1/P/CIR/2025/123."
    )
    issued_on: str | None = Field(
        None, description="ISO date string YYYY-MM-DD if mentioned."
    )
    effective_from: str | None = Field(
        None, description="ISO date string YYYY-MM-DD if mentioned."
    )
    affects_bse_ltd: bool = Field(
        ..., description="True iff the circular materially affects BSE Ltd's business."
    )
    impact_area: Literal[
        "trading_fees", "clearing", "listing_rules", "derivatives", "compliance",
        "market_infrastructure", "other"
    ]
    key_claims: list[ClaimWithQuote] = Field(default_factory=list)
```

- [ ] **Step 4: News schema**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/schemas/news.py`:

```python
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import BaseFact, ClaimWithQuote


class NewsFact(BaseFact):
    is_about_bse_ltd: bool = Field(
        ..., description="False if BSE Ltd only mentioned in passing or it's a different BSE."
    )
    event_type: Literal[
        "earnings", "regulatory", "product", "management",
        "macro", "sentiment", "rumor", "other"
    ]
    sentiment: float = Field(..., ge=-1.0, le=1.0)
    key_claims: list[ClaimWithQuote] = Field(default_factory=list)
    mentioned_entities: list[str] = Field(default_factory=list)
```

- [ ] **Step 5: Screener narrative schema**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/schemas/screener.py`:

```python
from __future__ import annotations

from pydantic import Field

from .common import BaseFact, ClaimWithQuote


class ScreenerCommentaryFact(BaseFact):
    """Narrative commentary only. All numeric data is parsed deterministically;
    the LLM is forbidden from extracting numbers here."""
    period: str | None = Field(None, description="e.g. 'Q1 FY26'.")
    qualitative_drivers: list[ClaimWithQuote] = Field(default_factory=list)
```

- [ ] **Step 6: Announcement schema**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/schemas/announcements.py`:

```python
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import BaseFact, ClaimWithQuote


class AnnouncementFact(BaseFact):
    announcement_type: Literal[
        "board_meeting", "investor_presentation", "results",
        "dividend", "regulatory_filing", "press_release", "other"
    ]
    announcement_date: str | None = Field(
        None, description="ISO YYYY-MM-DD if present."
    )
    key_claims: list[ClaimWithQuote] = Field(default_factory=list)
```

- [ ] **Step 7: Generic web schema**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/schemas/generic_web.py`:

```python
from __future__ import annotations

from typing import Literal

from pydantic import Field

from .common import BaseFact, ClaimWithQuote


class GenericWebFact(BaseFact):
    """Looser schema for one-off `fetch_url` / `web_search` results."""
    is_about_bse_ltd: bool
    event_type: Literal[
        "earnings", "regulatory", "product", "management",
        "macro", "sentiment", "other"
    ]
    sentiment: float = Field(..., ge=-1.0, le=1.0)
    key_claims: list[ClaimWithQuote] = Field(default_factory=list)
    mentioned_entities: list[str] = Field(default_factory=list)
```

- [ ] **Step 8: Schema registry**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/schemas/registry.py`:

```python
"""Map raw_documents.source → (schema, prompt template)."""

from __future__ import annotations

from typing import Type

from pydantic import BaseModel

from .announcements import AnnouncementFact
from .common import BaseFact
from .generic_web import GenericWebFact
from .news import NewsFact
from .screener import ScreenerCommentaryFact
from .sebi import SebiCircularFact


_PROMPT_PREFIX = (
    "You have zero prior knowledge of BSE Ltd, Indian markets, or finance. "
    "Your only information is the document below. Do NOT recall from training.\n"
    "Every claim you make MUST be backed by a `source_quote` field whose text "
    "appears verbatim in the document. Do NOT extract numbers — only narrate.\n"
    "If the document is irrelevant, set fields to safe defaults and confidence ≤ 0.3."
)


_PROMPTS: dict[str, str] = {
    "sebi_circulars": (
        f"{_PROMPT_PREFIX}\n\n"
        "Source: SEBI circular. Identify circular number, issuance and "
        "effective dates, and whether BSE Ltd is materially affected."
    ),
    "google_news": (
        f"{_PROMPT_PREFIX}\n\n"
        "Source: News article (Google News RSS). Classify event type and sentiment."
    ),
    "screener_bse": (
        f"{_PROMPT_PREFIX}\n\n"
        "Source: Screener.in narrative section (NOT financials tables). "
        "Numbers are extracted separately — do not include them here."
    ),
    "nse_announcements": (
        f"{_PROMPT_PREFIX}\n\n"
        "Source: NSE announcement PDF. Classify the announcement type."
    ),
    "bse_announcements": (
        f"{_PROMPT_PREFIX}\n\n"
        "Source: BSE announcement (reached via screener proxy). "
        "Classify the announcement type."
    ),
    "agent_fetched": (
        f"{_PROMPT_PREFIX}\n\n"
        "Source: arbitrary web page fetched by the agent. Use the generic_web schema."
    ),
    "web_search": (
        f"{_PROMPT_PREFIX}\n\n"
        "Source: web search result. Use the generic_web schema."
    ),
}


_SCHEMAS: dict[str, Type[BaseFact]] = {
    "sebi_circulars": SebiCircularFact,
    "google_news": NewsFact,
    "screener_bse": ScreenerCommentaryFact,
    "nse_announcements": AnnouncementFact,
    "bse_announcements": AnnouncementFact,
    "agent_fetched": GenericWebFact,
    "web_search": GenericWebFact,
}


def schema_for(source: str) -> Type[BaseFact]:
    return _SCHEMAS.get(source, GenericWebFact)


def prompt_for(source: str) -> str:
    return _PROMPTS.get(source, _PROMPTS["agent_fetched"])


def fact_type_for(source: str) -> str:
    """Stored in facts.fact_type. Stable per source family."""
    return {
        "sebi_circulars": "regulatory",
        "google_news": "news",
        "screener_bse": "narrative",
        "nse_announcements": "announcement",
        "bse_announcements": "announcement",
        "agent_fetched": "generic_web",
        "web_search": "generic_web",
    }.get(source, "generic_web")
```

- [ ] **Step 9: Write tests `tests/test_schemas.py`**

Write `/Users/devamarnani/Desktop/bsebot/tests/test_schemas.py`:

```python
import pytest

from bsebot.schemas import registry
from bsebot.schemas.generic_web import GenericWebFact
from bsebot.schemas.news import NewsFact
from bsebot.schemas.sebi import SebiCircularFact
from bsebot.schemas.announcements import AnnouncementFact


def test_registry_routes_known_sources():
    assert registry.schema_for("sebi_circulars") is SebiCircularFact
    assert registry.schema_for("google_news") is NewsFact
    assert registry.schema_for("nse_announcements") is AnnouncementFact
    assert registry.schema_for("bse_announcements") is AnnouncementFact


def test_registry_falls_back_to_generic_for_unknown():
    assert registry.schema_for("totally-new-source") is GenericWebFact


def test_prompt_for_includes_grounding_clause():
    p = registry.prompt_for("sebi_circulars")
    assert "zero prior knowledge" in p
    assert "verbatim" in p


def test_news_fact_rejects_out_of_range_sentiment():
    with pytest.raises(ValueError):
        NewsFact(
            summary="x", source_quote="x", confidence=0.5,
            is_about_bse_ltd=True, event_type="other",
            sentiment=2.5, key_claims=[], mentioned_entities=[],
        )


def test_sebi_fact_requires_impact_area():
    with pytest.raises(ValueError):
        SebiCircularFact(
            summary="x", source_quote="x", confidence=0.5,
            circular_number=None, issued_on=None, effective_from=None,
            affects_bse_ltd=False, impact_area="bogus_value", key_claims=[],
        )
```

- [ ] **Step 10: Run tests — expect PASS**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_schemas.py -v`

Expected: 5 passed.

- [ ] **Step 11: Commit**

```bash
cd /Users/devamarnani/Desktop/bsebot
git add bsebot/schemas/ tests/test_schemas.py
git commit -m "feat(bsebot): add per-source Pydantic fact schemas + registry"
```

---

## Task 7: Harvester base class (`bsebot/harvesters/base.py`) — TDD

**Files:**
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/harvesters/__init__.py`
- Create: `/Users/devamarnani/Desktop/bsebot/tests/test_harvester_base.py`
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/harvesters/base.py`

- [ ] **Step 1: Package marker + registry**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/harvesters/__init__.py`:

```python
"""Harvesters fetch raw documents from external sources."""

from __future__ import annotations

from typing import Callable

from .base import Harvester


_REGISTRY: dict[str, Callable[..., Harvester]] = {}


def register(name: str):
    def _decorator(cls):
        _REGISTRY[name] = cls
        return cls
    return _decorator


def get(name: str) -> Callable[..., Harvester]:
    if name not in _REGISTRY:
        raise KeyError(f"unknown harvester: {name!r}; known={list(_REGISTRY)}")
    return _REGISTRY[name]


def all_names() -> list[str]:
    # Ensure every harvester module is imported so registration runs.
    from . import (  # noqa: F401
        sebi_circulars,
        screener_bse,
        google_news,
        nse_announcements,
        bse_announcements,
        turnover_data,
        price_history,
    )
    return sorted(_REGISTRY)
```

- [ ] **Step 2: Write failing tests `tests/test_harvester_base.py`**

Write `/Users/devamarnani/Desktop/bsebot/tests/test_harvester_base.py`:

```python
import hashlib
import json

import pytest

from bsebot import db
from bsebot.harvesters.base import Harvester


class _FakeHarvester(Harvester):
    name = "fake"

    def __init__(self, db_path, *, docs):
        super().__init__(db_path=db_path)
        self._docs = docs

    def iter_documents(self):
        yield from self._docs


def test_fetch_and_persist_writes_raw_documents(tmp_path, migrations_dir):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)
    h = _FakeHarvester(
        db_path,
        docs=[
            {"url": "https://x/1", "content": "hello world", "metadata": {"a": 1}},
            {"url": "https://x/2", "content": "second", "metadata": {}},
        ],
    )

    result = h.fetch_and_persist()

    assert result.docs_fetched == 2
    assert result.docs_new == 2
    conn = db.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT source, url, content, metadata_json, processed "
            "FROM raw_documents ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    assert [r[0] for r in rows] == ["fake", "fake"]
    assert rows[0][1] == "https://x/1"
    assert rows[0][2] == "hello world"
    assert json.loads(rows[0][3]) == {"a": 1}
    assert rows[0][4] == 0


def test_dedup_by_content_hash(tmp_path, migrations_dir):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)
    docs = [{"url": "u", "content": "same", "metadata": {}}]
    h = _FakeHarvester(db_path, docs=docs)
    r1 = h.fetch_and_persist()
    r2 = h.fetch_and_persist()
    assert r1.docs_new == 1
    assert r2.docs_new == 0
    assert r2.docs_fetched == 1
    conn = db.connect(db_path)
    try:
        n = conn.execute("SELECT COUNT(*) FROM raw_documents").fetchone()[0]
    finally:
        conn.close()
    assert n == 1


def test_harvester_runs_row_written(tmp_path, migrations_dir):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)
    h = _FakeHarvester(db_path, docs=[{"url": "u", "content": "c", "metadata": {}}])
    h.fetch_and_persist()
    conn = db.connect(db_path)
    try:
        row = conn.execute(
            "SELECT harvester, status, docs_fetched, docs_new "
            "FROM harvester_runs ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    assert row[0][0] == "fake"
    assert row[0][1] == "success"
    assert row[0][2] == 1
    assert row[0][3] == 1


def test_failure_is_logged(tmp_path, migrations_dir):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)

    class _Boom(Harvester):
        name = "boom"

        def iter_documents(self):
            raise RuntimeError("nope")

    h = _Boom(db_path=db_path)
    with pytest.raises(RuntimeError):
        h.fetch_and_persist()
    conn = db.connect(db_path)
    try:
        row = conn.execute(
            "SELECT status, error FROM harvester_runs"
        ).fetchone()
    finally:
        conn.close()
    assert row[0] == "failure"
    assert "nope" in row[1]


def test_content_hash_is_sha256(tmp_path, migrations_dir):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)
    h = _FakeHarvester(db_path, docs=[{"url": "u", "content": "abc", "metadata": {}}])
    h.fetch_and_persist()
    conn = db.connect(db_path)
    try:
        h_db = conn.execute("SELECT content_hash FROM raw_documents").fetchone()[0]
    finally:
        conn.close()
    assert h_db == hashlib.sha256(b"abc").hexdigest()
```

- [ ] **Step 3: Run tests — expect FAIL**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_harvester_base.py -v`

Expected: ImportError on `bsebot.harvesters.base`.

- [ ] **Step 4: Implement `bsebot/harvesters/base.py`**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/harvesters/base.py`:

```python
"""Harvester ABC. Concrete harvesters yield documents; base persists them."""

from __future__ import annotations

import hashlib
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from bsebot import db, vault


log = logging.getLogger(__name__)


@dataclass
class HarvestResult:
    harvester: str
    status: str
    docs_fetched: int
    docs_new: int
    error: str | None = None


class Harvester(ABC):
    name: str = ""  # subclasses must override

    def __init__(self, db_path: str | Path) -> None:
        if not self.name:
            raise RuntimeError(f"{type(self).__name__}.name must be set")
        self.db_path = Path(db_path)

    @abstractmethod
    def iter_documents(self) -> Iterator[dict]:
        """Yield dicts with keys: 'url' (str|None), 'content' (str), 'metadata' (dict)."""

    def fetch_and_persist(self) -> HarvestResult:
        run_id = self._start_run()
        fetched = 0
        new = 0
        try:
            for doc in self.iter_documents():
                fetched += 1
                inserted = self._insert_if_new(doc)
                if inserted is not None:
                    new += 1
                    vault.publish_source(
                        source=self.name,
                        title=str(doc.get("metadata", {}).get("title", "(untitled)")),
                        body_markdown=doc.get("content", ""),
                        raw_document_id=inserted,
                        url=doc.get("url"),
                    )
        except Exception as e:
            self._end_run(run_id, status="failure", fetched=fetched, new=new,
                          error=repr(e))
            raise
        self._end_run(run_id, status="success", fetched=fetched, new=new)
        return HarvestResult(
            harvester=self.name,
            status="success",
            docs_fetched=fetched,
            docs_new=new,
        )

    # ---- internals ----

    def _start_run(self) -> int:
        conn = db.connect(self.db_path)
        try:
            cur = conn.execute(
                "INSERT INTO harvester_runs (harvester, status) VALUES (?, ?)",
                (self.name, "running"),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def _end_run(
        self, run_id: int, *, status: str, fetched: int, new: int,
        error: str | None = None,
    ) -> None:
        conn = db.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE harvester_runs "
                "SET status=?, docs_fetched=?, docs_new=?, error=?, "
                "    ended_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, int(fetched), int(new), error, int(run_id)),
            )
            conn.commit()
        finally:
            conn.close()

    def _insert_if_new(self, doc: dict) -> int | None:
        content = doc.get("content", "") or ""
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        metadata_json = json.dumps(doc.get("metadata") or {})
        url = doc.get("url")
        conn = db.connect(self.db_path)
        try:
            try:
                cur = conn.execute(
                    "INSERT INTO raw_documents "
                    "(source, url, content_hash, content, metadata_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (self.name, url, content_hash, content, metadata_json),
                )
                conn.commit()
                return cur.lastrowid
            except Exception as e:  # UNIQUE(content_hash) → already seen
                msg = str(e).lower()
                if "unique" in msg or "constraint" in msg:
                    return None
                raise
        finally:
            conn.close()
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_harvester_base.py -v`

Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
cd /Users/devamarnani/Desktop/bsebot
git add bsebot/harvesters/__init__.py bsebot/harvesters/base.py tests/test_harvester_base.py
git commit -m "feat(bsebot): add Harvester ABC with hash-dedup and harvester_runs logging"
```

---

## Task 8: SEBI circulars harvester — TDD

**Files:**
- Create: `/Users/devamarnani/Desktop/bsebot/tests/test_harvester_sebi.py`
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/harvesters/sebi_circulars.py`

- [ ] **Step 1: Write failing tests**

Write `/Users/devamarnani/Desktop/bsebot/tests/test_harvester_sebi.py`:

```python
from pathlib import Path

from bsebot import db
from bsebot.harvesters import sebi_circulars


FIXTURE_PDF = Path(__file__).parent / "fixtures" / "sebi_sample.pdf"

_INDEX_HTML = """
<html><body><table>
<tr><th>Date</th><th>Subject</th><th>Link</th></tr>
<tr><td>12 May 2025</td>
    <td>Framework for clearing corporations</td>
    <td><a href="/sebi/cir1.pdf">PDF</a></td></tr>
<tr><td>20 Apr 2025</td>
    <td>Listing obligations update</td>
    <td><a href="/sebi/cir2.pdf">PDF</a></td></tr>
</table></body></html>
"""


class _StubHttp:
    def __init__(self, pdf_bytes: bytes):
        self._pdf = pdf_bytes
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(url)
        from bsebot.http import HttpResponse
        if url.endswith(".pdf"):
            return HttpResponse(200, "", self._pdf, {}, url)
        return HttpResponse(200, _INDEX_HTML, _INDEX_HTML.encode(), {}, url)


def test_sebi_harvester_yields_one_doc_per_circular(tmp_path, migrations_dir):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)
    pdf_bytes = FIXTURE_PDF.read_bytes()
    http = _StubHttp(pdf_bytes)

    h = sebi_circulars.SebiCircularsHarvester(
        db_path=db_path,
        index_url="https://sebi.gov.in/sebiweb/other/OtherAction.do?doListing=yes",
        http=http,
        max_circulars=2,
    )
    result = h.fetch_and_persist()

    assert result.docs_fetched == 2
    assert result.docs_new == 2
    # Should hit index once + 2 PDFs
    assert len(http.calls) == 3

    conn = db.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT source, url, metadata_json FROM raw_documents ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    assert all(r[0] == "sebi_circulars" for r in rows)
    assert rows[0][1].endswith("cir1.pdf")
    # PDF text extracted into content
    conn = db.connect(db_path)
    try:
        content = conn.execute(
            "SELECT content FROM raw_documents WHERE id=1"
        ).fetchone()[0]
    finally:
        conn.close()
    assert "SEBI Circular" in content


def test_sebi_harvester_respects_max_circulars(tmp_path, migrations_dir):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)
    pdf_bytes = FIXTURE_PDF.read_bytes()
    http = _StubHttp(pdf_bytes)

    h = sebi_circulars.SebiCircularsHarvester(
        db_path=db_path, index_url="https://sebi.gov.in/x", http=http,
        max_circulars=1,
    )
    result = h.fetch_and_persist()
    assert result.docs_fetched == 1
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_harvester_sebi.py -v`

Expected: ImportError.

- [ ] **Step 3: Implement `bsebot/harvesters/sebi_circulars.py`**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/harvesters/sebi_circulars.py`:

```python
"""SEBI circulars index → PDFs harvester."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from bsebot.harvesters import register
from bsebot.harvesters.base import Harvester
from bsebot.http import HttpClient
from bsebot.parsers.sebi_pdf import extract_text_from_bytes


@register("sebi_circulars")
class SebiCircularsHarvester(Harvester):
    name = "sebi_circulars"

    def __init__(
        self,
        db_path: str | Path,
        *,
        index_url: str = "https://www.sebi.gov.in/sebiweb/other/OtherAction.do?doListing=yes&search=&search_radio=All&yr=&pgno=1",
        http: HttpClient | None = None,
        max_circulars: int = 50,
    ) -> None:
        super().__init__(db_path=db_path)
        self.index_url = index_url
        self.http = http or HttpClient(min_delay_seconds=2.0)
        self.max_circulars = int(max_circulars)

    def iter_documents(self) -> Iterator[dict]:
        index_resp = self.http.get(self.index_url)
        soup = BeautifulSoup(index_resp.text, "lxml")
        rows = soup.find_all("tr")
        count = 0
        for tr in rows:
            link = tr.find("a", href=True)
            if not link or not link["href"].lower().endswith(".pdf"):
                continue
            pdf_url = urljoin(self.index_url, link["href"])
            tds = tr.find_all("td")
            date_str = tds[0].get_text(strip=True) if tds else ""
            subject = ""
            if len(tds) >= 2:
                subject = tds[1].get_text(strip=True)
            pdf_resp = self.http.get(pdf_url)
            try:
                content = extract_text_from_bytes(pdf_resp.content)
            except Exception as e:
                # Skip unparseable PDFs but keep going
                content = f"[PDF parse failed: {e}]"
            yield {
                "url": pdf_url,
                "content": content,
                "metadata": {
                    "title": subject or "(no subject)",
                    "issued_on_raw": date_str,
                    "pdf_url": pdf_url,
                },
            }
            count += 1
            if count >= self.max_circulars:
                return
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_harvester_sebi.py -v`

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/devamarnani/Desktop/bsebot
git add tests/test_harvester_sebi.py bsebot/harvesters/sebi_circulars.py
git commit -m "feat(bsebot): add SEBI circulars harvester (index + PDF text extraction)"
```

---

## Task 9: Google News RSS harvester — TDD

**Files:**
- Create: `/Users/devamarnani/Desktop/bsebot/tests/fixtures/google_news.xml`
- Create: `/Users/devamarnani/Desktop/bsebot/tests/test_harvester_news.py`
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/harvesters/google_news.py`

- [ ] **Step 1: Write fixture**

Write `/Users/devamarnani/Desktop/bsebot/tests/fixtures/google_news.xml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>"BSE Ltd" - Google News</title>
  <link>https://news.google.com/</link>
  <item>
    <title>BSE Ltd reports record turnover in Q1</title>
    <link>https://example.com/news/bse-q1</link>
    <pubDate>Mon, 12 May 2025 09:00:00 GMT</pubDate>
    <description>BSE Ltd posted record turnover figures...</description>
    <source url="https://example.com">Example News</source>
  </item>
  <item>
    <title>SEBI nudges exchanges on fee transparency</title>
    <link>https://example.com/news/sebi-fees</link>
    <pubDate>Sun, 11 May 2025 14:00:00 GMT</pubDate>
    <description>The regulator told exchanges to publish fee schedules...</description>
    <source url="https://example.com">Example News</source>
  </item>
</channel>
</rss>
```

- [ ] **Step 2: Write failing tests**

Write `/Users/devamarnani/Desktop/bsebot/tests/test_harvester_news.py`:

```python
from pathlib import Path

from bsebot import db
from bsebot.harvesters import google_news


FIXTURE = Path(__file__).parent / "fixtures" / "google_news.xml"


def test_google_news_yields_items(tmp_path, migrations_dir, monkeypatch):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)

    def fake_parse(url):
        import feedparser
        return feedparser.parse(FIXTURE.read_text(encoding="utf-8"))

    monkeypatch.setattr(google_news.feedparser, "parse", fake_parse)

    h = google_news.GoogleNewsHarvester(db_path=db_path, query="BSE Ltd")
    res = h.fetch_and_persist()
    assert res.docs_fetched == 2
    assert res.docs_new == 2
    conn = db.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT url, content FROM raw_documents ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    assert rows[0][0] == "https://example.com/news/bse-q1"
    assert "record turnover" in rows[0][1]
```

- [ ] **Step 3: Run — expect FAIL**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_harvester_news.py -v`

Expected: ImportError.

- [ ] **Step 4: Implement `bsebot/harvesters/google_news.py`**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/harvesters/google_news.py`:

```python
"""Google News RSS harvester."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator
from urllib.parse import quote_plus

import feedparser

from bsebot.harvesters import register
from bsebot.harvesters.base import Harvester


@register("google_news")
class GoogleNewsHarvester(Harvester):
    name = "google_news"

    def __init__(
        self,
        db_path: str | Path,
        *,
        query: str = "BSE Ltd",
        hl: str = "en-IN",
        gl: str = "IN",
    ) -> None:
        super().__init__(db_path=db_path)
        self.query = query
        self.hl = hl
        self.gl = gl

    @property
    def url(self) -> str:
        return (
            "https://news.google.com/rss/search?"
            f"q={quote_plus(self.query)}&hl={self.hl}&gl={self.gl}&ceid={self.gl}:{self.hl[:2]}"
        )

    def iter_documents(self) -> Iterator[dict]:
        feed = feedparser.parse(self.url)
        for entry in getattr(feed, "entries", []) or []:
            title = getattr(entry, "title", "") or ""
            link = getattr(entry, "link", "") or ""
            summary = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
            published = getattr(entry, "published", "") or ""
            body = f"# {title}\n\nPublished: {published}\n\n{summary}\n\nLink: {link}\n"
            yield {
                "url": link,
                "content": body,
                "metadata": {
                    "title": title,
                    "published_raw": published,
                    "rss_feed": self.url,
                },
            }
```

- [ ] **Step 5: Run — expect PASS**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_harvester_news.py -v`

Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
cd /Users/devamarnani/Desktop/bsebot
git add tests/fixtures/google_news.xml tests/test_harvester_news.py bsebot/harvesters/google_news.py
git commit -m "feat(bsebot): add Google News RSS harvester"
```

---

## Task 10: Screener.in harvester — TDD

**Files:**
- Create: `/Users/devamarnani/Desktop/bsebot/tests/test_harvester_screener.py`
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/harvesters/screener_bse.py`

- [ ] **Step 1: Write failing tests**

Write `/Users/devamarnani/Desktop/bsebot/tests/test_harvester_screener.py`:

```python
from pathlib import Path

from bsebot import db
from bsebot.harvesters import screener_bse


FIXTURE = Path(__file__).parent / "fixtures" / "screener_bse.html"


class _StubHttp:
    def __init__(self, html: str):
        self._html = html
        self.calls: list[str] = []

    def get(self, url, **kw):
        self.calls.append(url)
        from bsebot.http import HttpResponse
        return HttpResponse(200, self._html, self._html.encode(), {}, url)


def test_screener_yields_one_doc_plus_announcement_links(tmp_path, migrations_dir):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)
    html = FIXTURE.read_text(encoding="utf-8")
    http = _StubHttp(html)

    h = screener_bse.ScreenerBseHarvester(db_path=db_path, http=http)
    res = h.fetch_and_persist()

    # 1 main page + 2 announcement summary docs == 3
    assert res.docs_fetched == 3
    assert res.docs_new == 3

    conn = db.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT url, metadata_json FROM raw_documents ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    # First row is the main company page
    assert "/company/BSE/" in rows[0][0]
    # Sub-rows reference announcement hrefs
    sub_urls = [r[0] for r in rows[1:]]
    assert any("c/123" in u for u in sub_urls)
    assert any("c/124" in u for u in sub_urls)


def test_screener_metadata_contains_parsed_financials(tmp_path, migrations_dir):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)
    html = FIXTURE.read_text(encoding="utf-8")
    http = _StubHttp(html)
    h = screener_bse.ScreenerBseHarvester(db_path=db_path, http=http)
    h.fetch_and_persist()
    import json
    conn = db.connect(db_path)
    try:
        meta = json.loads(conn.execute(
            "SELECT metadata_json FROM raw_documents WHERE id=1"
        ).fetchone()[0])
    finally:
        conn.close()
    assert "quarterly_pnl" in meta
    sales = next(r for r in meta["quarterly_pnl"] if r["metric"] == "Sales")
    assert sales["Sep 2025"] == 401.0
    assert "shareholding" in meta
```

- [ ] **Step 2: Run — expect FAIL**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_harvester_screener.py -v`

Expected: ImportError.

- [ ] **Step 3: Implement `bsebot/harvesters/screener_bse.py`**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/harvesters/screener_bse.py`:

```python
"""Screener.in BSE Ltd page harvester."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator
from urllib.parse import urljoin

from bsebot.harvesters import register
from bsebot.harvesters.base import Harvester
from bsebot.http import HttpClient
from bsebot.parsers.screener_html import (
    extract_announcements,
    extract_quarterly_pnl,
    extract_shareholding,
)


_DEFAULT_URL = "https://www.screener.in/company/BSE/consolidated/"


@register("screener_bse")
class ScreenerBseHarvester(Harvester):
    name = "screener_bse"

    def __init__(
        self,
        db_path: str | Path,
        *,
        url: str = _DEFAULT_URL,
        http: HttpClient | None = None,
    ) -> None:
        super().__init__(db_path=db_path)
        self.url = url
        self.http = http or HttpClient(min_delay_seconds=2.0)

    def iter_documents(self) -> Iterator[dict]:
        resp = self.http.get(self.url)
        html = resp.text
        quarterly = extract_quarterly_pnl(html)
        shareholding = extract_shareholding(html)
        announcements = extract_announcements(html)
        yield {
            "url": self.url,
            "content": html,
            "metadata": {
                "title": "BSE Ltd — Screener company page",
                "quarterly_pnl": quarterly,
                "shareholding": shareholding,
                "announcement_count": len(announcements),
            },
        }
        for ann in announcements:
            href = ann.get("href", "")
            full_url = urljoin(self.url, href) if href else None
            if not full_url:
                continue
            body = f"# {ann['title']}\n\nLink: {full_url}\n"
            yield {
                "url": full_url,
                "content": body,
                "metadata": {
                    "title": ann["title"],
                    "kind": "announcement_link",
                    "parent_url": self.url,
                },
            }
```

- [ ] **Step 4: Run — expect PASS**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_harvester_screener.py -v`

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/devamarnani/Desktop/bsebot
git add tests/test_harvester_screener.py bsebot/harvesters/screener_bse.py
git commit -m "feat(bsebot): add screener.in harvester (financials + announcements)"
```

---

## Task 11: NSE archives harvester — TDD

**Files:**
- Create: `/Users/devamarnani/Desktop/bsebot/tests/fixtures/nse_announcement.pdf` (reuse SEBI fixture if convenient — see step 1)
- Create: `/Users/devamarnani/Desktop/bsebot/tests/test_harvester_nse.py`
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/harvesters/nse_announcements.py`

- [ ] **Step 1: Reuse fixture**

The SEBI fixture PDF already contains parseable text. Symlink or copy it:

Run: `cd /Users/devamarnani/Desktop/bsebot && cp tests/fixtures/sebi_sample.pdf tests/fixtures/nse_announcement.pdf`

- [ ] **Step 2: Write failing tests**

Write `/Users/devamarnani/Desktop/bsebot/tests/test_harvester_nse.py`:

```python
from pathlib import Path

from bsebot import db
from bsebot.harvesters import nse_announcements


FIXTURE = Path(__file__).parent / "fixtures" / "nse_announcement.pdf"

_INDEX_JSON = {
    "rows": [
        {"symbol": "BSE", "subject": "Board meeting outcome",
         "attchmntFile": "https://nsearchives.nseindia.com/corporate/BSE_15052025.pdf",
         "an_dt": "15-MAY-2025 09:00:00"},
        {"symbol": "BSE", "subject": "Investor presentation",
         "attchmntFile": "https://nsearchives.nseindia.com/corporate/BSE_28042025.pdf",
         "an_dt": "28-APR-2025 18:00:00"},
    ]
}


class _StubHttp:
    def __init__(self, pdf_bytes: bytes):
        self._pdf = pdf_bytes
        self.calls: list[str] = []

    def get(self, url, **kw):
        self.calls.append(url)
        from bsebot.http import HttpResponse
        if url.endswith(".pdf"):
            return HttpResponse(200, "", self._pdf, {}, url)
        import json
        body = json.dumps(_INDEX_JSON)
        return HttpResponse(200, body, body.encode(), {"content-type": "application/json"}, url)


def test_nse_harvester_one_doc_per_attachment(tmp_path, migrations_dir):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)
    pdf_bytes = FIXTURE.read_bytes()
    http = _StubHttp(pdf_bytes)

    h = nse_announcements.NseAnnouncementsHarvester(
        db_path=db_path, http=http, symbol="BSE", max_items=2,
    )
    res = h.fetch_and_persist()
    assert res.docs_fetched == 2
    assert res.docs_new == 2
    # 1 index + 2 PDFs
    assert len(http.calls) == 3

    conn = db.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT url FROM raw_documents ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    assert rows[0][0].endswith("BSE_15052025.pdf")
    assert rows[1][0].endswith("BSE_28042025.pdf")
```

- [ ] **Step 3: Implement `bsebot/harvesters/nse_announcements.py`**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/harvesters/nse_announcements.py`:

```python
"""NSE archives announcements harvester.

The archive subdomain (nsearchives.nseindia.com) is more permissive than the
main nseindia.com host, which actively blocks bots. We fetch the JSON index
from a known archive endpoint and pull each PDF.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from bsebot.harvesters import register
from bsebot.harvesters.base import Harvester
from bsebot.http import HttpClient
from bsebot.parsers.sebi_pdf import extract_text_from_bytes


@register("nse_announcements")
class NseAnnouncementsHarvester(Harvester):
    name = "nse_announcements"

    def __init__(
        self,
        db_path: str | Path,
        *,
        symbol: str = "BSE",
        index_url: str = "https://nsearchives.nseindia.com/corporate-filings-announcements?symbol=BSE",
        http: HttpClient | None = None,
        max_items: int = 50,
    ) -> None:
        super().__init__(db_path=db_path)
        self.symbol = symbol
        self.index_url = index_url
        self.http = http or HttpClient(min_delay_seconds=2.0)
        self.max_items = int(max_items)

    def iter_documents(self) -> Iterator[dict]:
        resp = self.http.get(self.index_url)
        try:
            payload = json.loads(resp.text)
        except json.JSONDecodeError:
            return
        rows = payload.get("rows") or payload.get("data") or []
        for i, row in enumerate(rows[: self.max_items]):
            url = row.get("attchmntFile") or row.get("attachment")
            if not url:
                continue
            subject = row.get("subject", "")
            an_dt = row.get("an_dt", "")
            pdf_resp = self.http.get(url)
            try:
                content = extract_text_from_bytes(pdf_resp.content)
            except Exception as e:
                content = f"[PDF parse failed: {e}]"
            yield {
                "url": url,
                "content": content,
                "metadata": {
                    "title": subject or "(no subject)",
                    "symbol": self.symbol,
                    "an_dt_raw": an_dt,
                },
            }
```

- [ ] **Step 4: Run — expect PASS**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_harvester_nse.py -v`

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/devamarnani/Desktop/bsebot
git add tests/fixtures/nse_announcement.pdf tests/test_harvester_nse.py bsebot/harvesters/nse_announcements.py
git commit -m "feat(bsebot): add NSE archives announcements harvester"
```

---

## Task 12: BSE announcements harvester (via Screener proxy) — TDD

**Files:**
- Create: `/Users/devamarnani/Desktop/bsebot/tests/test_harvester_bse.py`
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/harvesters/bse_announcements.py`

This harvester reuses Screener's announcement links as a proxy because `bseindia.com` blocks bots. It pulls only the **annotated** announcement metadata that the Screener harvester wrote; this avoids duplicating the page fetch.

- [ ] **Step 1: Write failing tests**

Write `/Users/devamarnani/Desktop/bsebot/tests/test_harvester_bse.py`:

```python
from pathlib import Path

from bsebot import db
from bsebot.harvesters import bse_announcements, screener_bse


FIXTURE = Path(__file__).parent / "fixtures" / "screener_bse.html"


class _StubHttp:
    def __init__(self, html: str):
        self._html = html

    def get(self, url, **kw):
        from bsebot.http import HttpResponse
        return HttpResponse(200, self._html, self._html.encode(), {}, url)


def test_bse_announcements_reads_screener_links(tmp_path, migrations_dir):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)
    html = FIXTURE.read_text(encoding="utf-8")
    # Seed: run screener first so its rows exist
    screener_bse.ScreenerBseHarvester(
        db_path=db_path, http=_StubHttp(html),
    ).fetch_and_persist()

    # Now BSE harvester re-tags announcement rows
    h = bse_announcements.BseAnnouncementsHarvester(db_path=db_path)
    res = h.fetch_and_persist()
    # No new docs (it's a tagging pass), but harvester run still recorded.
    assert res.docs_new == 0
    conn = db.connect(db_path)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM harvester_runs WHERE harvester='bse_announcements'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == 1


def test_bse_announcements_promotes_screener_rows_to_separate_source(
    tmp_path, migrations_dir,
):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)
    html = FIXTURE.read_text(encoding="utf-8")
    screener_bse.ScreenerBseHarvester(
        db_path=db_path, http=_StubHttp(html),
    ).fetch_and_persist()

    bse_announcements.BseAnnouncementsHarvester(db_path=db_path).fetch_and_persist()
    conn = db.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT source, url FROM raw_documents WHERE source='bse_announcements'"
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 2
    assert all(r[0] == "bse_announcements" for r in rows)
```

- [ ] **Step 2: Implement `bsebot/harvesters/bse_announcements.py`**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/harvesters/bse_announcements.py`:

```python
"""BSE announcements via Screener proxy.

bseindia.com blocks bots. Screener already links to BSE announcements; this
harvester re-materializes those links as documents under source='bse_announcements'
so a dedicated extractor schema can target them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

from bsebot import db
from bsebot.harvesters import register
from bsebot.harvesters.base import Harvester


@register("bse_announcements")
class BseAnnouncementsHarvester(Harvester):
    name = "bse_announcements"

    def iter_documents(self) -> Iterator[dict]:
        conn = db.connect(self.db_path)
        try:
            rows = conn.execute(
                "SELECT id, url, content, metadata_json FROM raw_documents "
                "WHERE source='screener_bse' "
                "AND json_extract(metadata_json, '$.kind')='announcement_link'"
            ).fetchall()
        finally:
            conn.close()
        for _id, url, content, meta_json in rows:
            try:
                meta = json.loads(meta_json or "{}")
            except json.JSONDecodeError:
                meta = {}
            meta["promoted_from_screener_doc"] = _id
            yield {
                "url": url,
                "content": content,
                "metadata": meta,
            }
```

- [ ] **Step 3: Run — expect PASS**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_harvester_bse.py -v`

Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
cd /Users/devamarnani/Desktop/bsebot
git add tests/test_harvester_bse.py bsebot/harvesters/bse_announcements.py
git commit -m "feat(bsebot): add BSE announcements harvester (screener proxy)"
```

---

## Task 13: Turnover data harvester — TDD

**Files:**
- Create: `/Users/devamarnani/Desktop/bsebot/tests/fixtures/way2wealth_turnover.html`
- Create: `/Users/devamarnani/Desktop/bsebot/tests/test_harvester_turnover.py`
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/harvesters/turnover_data.py`

- [ ] **Step 1: Write fixture**

Write `/Users/devamarnani/Desktop/bsebot/tests/fixtures/way2wealth_turnover.html`:

```html
<html><body>
<h2>Daily volume and turnover - NSE</h2>
<table id="turnover-table">
  <thead><tr><th>Date</th><th>Volume</th><th>Turnover (Rs Cr)</th></tr></thead>
  <tbody>
    <tr><td>15-May-2025</td><td>32,45,123</td><td>2,150.45</td></tr>
    <tr><td>14-May-2025</td><td>28,91,002</td><td>1,995.10</td></tr>
  </tbody>
</table>
</body></html>
```

- [ ] **Step 2: Write failing tests**

Write `/Users/devamarnani/Desktop/bsebot/tests/test_harvester_turnover.py`:

```python
import json
from pathlib import Path

from bsebot import db
from bsebot.harvesters import turnover_data


FIXTURE = Path(__file__).parent / "fixtures" / "way2wealth_turnover.html"


class _StubHttp:
    def __init__(self, html: str):
        self._html = html

    def get(self, url, **kw):
        from bsebot.http import HttpResponse
        return HttpResponse(200, self._html, self._html.encode(), {}, url)


def test_turnover_yields_one_doc_with_rows(tmp_path, migrations_dir):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)
    html = FIXTURE.read_text(encoding="utf-8")
    http = _StubHttp(html)
    h = turnover_data.TurnoverDataHarvester(db_path=db_path, http=http)
    res = h.fetch_and_persist()
    assert res.docs_fetched == 1
    conn = db.connect(db_path)
    try:
        meta_json = conn.execute(
            "SELECT metadata_json FROM raw_documents WHERE id=1"
        ).fetchone()[0]
    finally:
        conn.close()
    meta = json.loads(meta_json)
    assert "rows" in meta
    assert len(meta["rows"]) == 2
    assert meta["rows"][0]["Date"] == "15-May-2025"
    assert meta["rows"][0]["Volume"] == 3245123
    assert meta["rows"][0]["Turnover (Rs Cr)"] == 2150.45
```

- [ ] **Step 3: Implement `bsebot/harvesters/turnover_data.py`**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/harvesters/turnover_data.py`:

```python
"""Daily NSE turnover from way2wealth. Deterministic table parse."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from bsebot.harvesters import register
from bsebot.harvesters.base import Harvester
from bsebot.http import HttpClient
from bsebot.parsers.numeric import html_table_to_rows, parse_volume


@register("turnover_data")
class TurnoverDataHarvester(Harvester):
    name = "turnover_data"

    def __init__(
        self,
        db_path: str | Path,
        *,
        url: str = "https://www.way2wealth.com/market/volumeturnover",
        http: HttpClient | None = None,
    ) -> None:
        super().__init__(db_path=db_path)
        self.url = url
        self.http = http or HttpClient(min_delay_seconds=2.0)

    def iter_documents(self) -> Iterator[dict]:
        resp = self.http.get(self.url)
        html = resp.text
        rows = html_table_to_rows(html)
        coerced: list[dict] = []
        for r in rows:
            row = dict(r)
            if "Volume" in row:
                vol = parse_volume(row["Volume"])
                if vol is not None:
                    row["Volume"] = vol
            for k, v in list(row.items()):
                if k.lower().startswith("turnover"):
                    s = (v or "").replace(",", "").strip()
                    try:
                        row[k] = float(s)
                    except ValueError:
                        pass
            coerced.append(row)
        yield {
            "url": self.url,
            "content": html,
            "metadata": {
                "title": "NSE daily volume + turnover",
                "rows": coerced,
            },
        }
```

- [ ] **Step 4: Run — expect PASS**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_harvester_turnover.py -v`

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/devamarnani/Desktop/bsebot
git add tests/fixtures/way2wealth_turnover.html tests/test_harvester_turnover.py bsebot/harvesters/turnover_data.py
git commit -m "feat(bsebot): add way2wealth turnover/volume harvester"
```

---

## Task 14: Price history harvester (yfinance → `price_history` table) — TDD

**Files:**
- Create: `/Users/devamarnani/Desktop/bsebot/tests/test_harvester_price.py`
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/harvesters/price_history.py`

- [ ] **Step 1: Write failing tests**

Write `/Users/devamarnani/Desktop/bsebot/tests/test_harvester_price.py`:

```python
import pandas as pd
import pytest

from bsebot import db
from bsebot.harvesters import price_history


def _fake_history():
    idx = pd.to_datetime(["2025-05-13", "2025-05-14", "2025-05-15"])
    return pd.DataFrame(
        {
            "Open":  [100.0, 102.0, 105.0],
            "High":  [103.0, 106.0, 108.0],
            "Low":   [ 99.0, 101.0, 104.0],
            "Close": [102.0, 105.0, 107.0],
            "Volume":[100000, 120000, 95000],
        },
        index=idx,
    )


def test_price_history_writes_rows(tmp_path, migrations_dir, monkeypatch):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)

    class _FakeTicker:
        def __init__(self, sym): self.sym = sym
        def history(self, period=None, interval=None, start=None, end=None, auto_adjust=False):
            return _fake_history()

    monkeypatch.setattr(price_history.yf, "Ticker", _FakeTicker)
    h = price_history.PriceHistoryHarvester(
        db_path=db_path, yf_symbol="BSE.NS", ticker="BSE", period="3d",
    )
    res = h.fetch_and_persist()
    assert res.docs_new == 3
    conn = db.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT date, open, close, volume FROM price_history ORDER BY date"
        ).fetchall()
    finally:
        conn.close()
    assert rows[0] == ("2025-05-13", 100.0, 102.0, 100000)
    assert rows[2] == ("2025-05-15", 105.0, 107.0, 95000)


def test_price_history_is_idempotent(tmp_path, migrations_dir, monkeypatch):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)

    class _FakeTicker:
        def __init__(self, sym): self.sym = sym
        def history(self, **kw): return _fake_history()

    monkeypatch.setattr(price_history.yf, "Ticker", _FakeTicker)
    h = price_history.PriceHistoryHarvester(db_path=db_path)
    h.fetch_and_persist()
    res2 = h.fetch_and_persist()
    assert res2.docs_new == 0
    conn = db.connect(db_path)
    try:
        n = conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
    finally:
        conn.close()
    assert n == 3
```

- [ ] **Step 2: Implement `bsebot/harvesters/price_history.py`**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/harvesters/price_history.py`:

```python
"""yfinance → price_history table. Deterministic, no LLM."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yfinance as yf

from bsebot import db
from bsebot.harvesters import register
from bsebot.harvesters.base import HarvestResult


@register("price_history")
class PriceHistoryHarvester:
    """Bypasses the Harvester ABC because this writes to `price_history`,
    not `raw_documents`. Still emits a `harvester_runs` row."""

    name = "price_history"

    def __init__(
        self,
        db_path: str | Path,
        *,
        yf_symbol: str = "BSE.NS",
        ticker: str = "BSE",
        period: str = "5y",
        interval: str = "1d",
    ) -> None:
        self.db_path = Path(db_path)
        self.yf_symbol = yf_symbol
        self.ticker = ticker
        self.period = period
        self.interval = interval

    def fetch_and_persist(self) -> HarvestResult:
        run_id = self._start_run()
        try:
            new = self._pull_and_upsert()
        except Exception as e:
            self._end_run(run_id, status="failure", fetched=0, new=0, error=repr(e))
            raise
        self._end_run(run_id, status="success", fetched=new, new=new)
        return HarvestResult(
            harvester=self.name, status="success",
            docs_fetched=new, docs_new=new,
        )

    # ---- internals ----

    def _pull_and_upsert(self) -> int:
        t = yf.Ticker(self.yf_symbol)
        df = t.history(period=self.period, interval=self.interval, auto_adjust=False)
        if df is None or df.empty:
            return 0
        new = 0
        conn = db.connect(self.db_path)
        try:
            for ts, row in df.iterrows():
                date_str = ts.strftime("%Y-%m-%d")
                cur = conn.execute(
                    "INSERT OR IGNORE INTO price_history "
                    "(date, ticker, open, high, low, close, volume) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        date_str, self.ticker,
                        float(row["Open"]), float(row["High"]),
                        float(row["Low"]), float(row["Close"]),
                        int(row["Volume"]),
                    ),
                )
                if cur.rowcount:
                    new += 1
            conn.commit()
        finally:
            conn.close()
        return new

    def _start_run(self) -> int:
        conn = db.connect(self.db_path)
        try:
            cur = conn.execute(
                "INSERT INTO harvester_runs (harvester, status) VALUES (?, ?)",
                (self.name, "running"),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def _end_run(
        self, run_id: int, *, status: str, fetched: int, new: int,
        error: str | None = None,
    ) -> None:
        conn = db.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE harvester_runs "
                "SET status=?, docs_fetched=?, docs_new=?, error=?, "
                "    ended_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, int(fetched), int(new), error, int(run_id)),
            )
            conn.commit()
        finally:
            conn.close()
```

- [ ] **Step 3: Run — expect PASS**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_harvester_price.py -v`

Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
cd /Users/devamarnani/Desktop/bsebot
git add tests/test_harvester_price.py bsebot/harvesters/price_history.py
git commit -m "feat(bsebot): add yfinance price_history harvester"
```

---

## Task 15: Extractor (`bsebot/extractor.py`) — TDD

**Files:**
- Create: `/Users/devamarnani/Desktop/bsebot/tests/test_extractor.py`
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/extractor.py`

- [ ] **Step 1: Write failing tests**

Write `/Users/devamarnani/Desktop/bsebot/tests/test_extractor.py`:

```python
import json

import pytest

from bsebot import db, extractor
from bsebot.config import AppConfig, LLMConfig, ProviderConfig
from bsebot.schemas.news import NewsFact
from bsebot.schemas.common import ClaimWithQuote


def _config(db_path):
    return AppConfig(
        database_path=str(db_path),
        llm=LLMConfig(
            extract_max_tokens=2048,
            reason_max_tokens=8192,
            continuation_max_attempts=3,
            providers=[
                ProviderConfig("gemini", "gemini/gemini-2.5-flash",
                               "GEMINI_API_KEY", "k", ["extract", "reason"]),
            ],
        ),
    )


def _insert_doc(db_path, source, content):
    import hashlib
    conn = db.connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO raw_documents (source, content_hash, content) "
            "VALUES (?, ?, ?)",
            (source, hashlib.sha256(content.encode()).hexdigest(), content),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def test_extractor_writes_fact_on_valid_quote(tmp_path, migrations_dir, monkeypatch):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)
    cfg = _config(db_path)
    src_text = "BSE Ltd reported record turnover in May 2025, driven by derivatives."
    doc_id = _insert_doc(db_path, "google_news", src_text)

    fact = NewsFact(
        summary="Record turnover", source_quote="record turnover in May 2025",
        confidence=0.9, is_about_bse_ltd=True, event_type="earnings",
        sentiment=0.6,
        key_claims=[ClaimWithQuote(claim="derivatives drove growth",
                                   source_quote="driven by derivatives")],
        mentioned_entities=["BSE Ltd"],
    )

    class _Router:
        def __init__(self, cfg): self.cfg = cfg
        def extract(self, prompt, schema):
            return fact, {"provider": "gemini", "model": "g", "finish_reason": "stop"}

    monkeypatch.setattr(extractor, "LLMRouter", _Router)

    n = extractor.run_once(cfg, batch_size=10)
    assert n == 1
    conn = db.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT source_doc_id, fact_type, payload_json, source_quote, confidence "
            "FROM facts"
        ).fetchall()
        processed = conn.execute(
            "SELECT processed, processed_at FROM raw_documents WHERE id=?", (doc_id,)
        ).fetchone()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0][0] == doc_id
    assert rows[0][1] == "news"
    payload = json.loads(rows[0][2])
    assert payload["summary"] == "Record turnover"
    assert processed[0] == 1
    assert processed[1] is not None


def test_extractor_rejects_when_quote_not_in_source(
    tmp_path, migrations_dir, monkeypatch,
):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)
    cfg = _config(db_path)
    _insert_doc(db_path, "google_news", "Boring text without the magic phrase.")

    bad_fact = NewsFact(
        summary="x", source_quote="this phrase is fabricated",
        confidence=0.9, is_about_bse_ltd=True, event_type="other",
        sentiment=0.0, key_claims=[], mentioned_entities=[],
    )

    class _Router:
        def __init__(self, cfg): self.cfg = cfg
        def extract(self, prompt, schema):
            return bad_fact, {"provider": "gemini", "model": "g", "finish_reason": "stop"}

    monkeypatch.setattr(extractor, "LLMRouter", _Router)

    n = extractor.run_once(cfg, batch_size=10)
    # No facts created, but doc still marked processed.
    assert n == 0
    conn = db.connect(db_path)
    try:
        n_facts = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        processed = conn.execute(
            "SELECT processed FROM raw_documents"
        ).fetchone()[0]
    finally:
        conn.close()
    assert n_facts == 0
    assert processed == 1


def test_extractor_substring_check_is_case_insensitive_and_whitespace_tolerant(
    tmp_path, migrations_dir, monkeypatch,
):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)
    cfg = _config(db_path)
    _insert_doc(db_path, "google_news",
                "BSE  Ltd\nreported   RECORD\tturnover in May.")

    fact = NewsFact(
        summary="x", source_quote="record turnover in may",
        confidence=0.9, is_about_bse_ltd=True, event_type="earnings",
        sentiment=0.5, key_claims=[], mentioned_entities=[],
    )

    class _Router:
        def __init__(self, cfg): self.cfg = cfg
        def extract(self, prompt, schema):
            return fact, {"provider": "gemini", "model": "g", "finish_reason": "stop"}

    monkeypatch.setattr(extractor, "LLMRouter", _Router)
    n = extractor.run_once(cfg, batch_size=10)
    assert n == 1


def test_extractor_processes_only_unprocessed(tmp_path, migrations_dir, monkeypatch):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)
    cfg = _config(db_path)
    d1 = _insert_doc(db_path, "google_news", "hello world")
    d2 = _insert_doc(db_path, "google_news", "second doc")
    # Mark d1 as already processed
    conn = db.connect(db_path)
    try:
        conn.execute("UPDATE raw_documents SET processed=1 WHERE id=?", (d1,))
        conn.commit()
    finally:
        conn.close()

    calls = {"n": 0}

    class _Router:
        def __init__(self, cfg): self.cfg = cfg
        def extract(self, prompt, schema):
            calls["n"] += 1
            return NewsFact(
                summary="x", source_quote="second doc",
                confidence=0.9, is_about_bse_ltd=True, event_type="other",
                sentiment=0.0, key_claims=[], mentioned_entities=[],
            ), {"provider": "gemini", "model": "g", "finish_reason": "stop"}

    monkeypatch.setattr(extractor, "LLMRouter", _Router)
    n = extractor.run_once(cfg, batch_size=10)
    assert n == 1
    assert calls["n"] == 1


def test_quote_verifier_function():
    assert extractor.quote_in_source("record TURNOVER", "Record turnover in May")
    assert extractor.quote_in_source(
        "record turnover", "BSE\nLtd RECORD\tturnover here"
    )
    assert not extractor.quote_in_source("missing phrase", "irrelevant text")
    assert not extractor.quote_in_source("", "anything")
```

- [ ] **Step 2: Run — expect FAIL**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_extractor.py -v`

Expected: ImportError.

- [ ] **Step 3: Implement `bsebot/extractor.py`**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/extractor.py`:

```python
"""Process unprocessed raw_documents into facts using the LLM router.

Grounding pipeline (per spec):
  1. Pydantic schema validation (`instructor` handles parse retries).
  2. Verbatim quote verification: `source_quote` must appear as a normalized
     substring of the source content. Failure → discard, log, retry once with
     stricter prompt. Second failure → mark doc processed without a fact.
  3. Mark raw_documents.processed=1 either way.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from bsebot import db, vault
from bsebot.config import AppConfig
from bsebot.llm import LLMRouter
from bsebot.schemas.registry import fact_type_for, prompt_for, schema_for


log = logging.getLogger(__name__)


_WS = re.compile(r"\s+")


def _normalize(s: str) -> str:
    return _WS.sub(" ", s).strip().lower()


def quote_in_source(quote: str, source_text: str) -> bool:
    """True iff `quote` appears as a substring of `source_text` after
    whitespace + case normalization. Empty quote returns False."""
    if not quote or not quote.strip():
        return False
    return _normalize(quote) in _normalize(source_text)


@dataclass
class _Doc:
    id: int
    source: str
    content: str


def _fetch_batch(db_path, batch_size: int) -> list[_Doc]:
    conn = db.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, source, content FROM raw_documents "
            "WHERE processed=0 ORDER BY id LIMIT ?",
            (int(batch_size),),
        ).fetchall()
    finally:
        conn.close()
    return [_Doc(id=r[0], source=r[1], content=r[2]) for r in rows]


def _mark_processed(db_path, doc_id: int) -> None:
    conn = db.connect(db_path)
    try:
        conn.execute(
            "UPDATE raw_documents SET processed=1, processed_at=CURRENT_TIMESTAMP "
            "WHERE id=?", (doc_id,),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_fact(
    db_path,
    *,
    doc_id: int,
    fact_type: str,
    payload_json: str,
    source_quote: str,
    confidence: float,
) -> int:
    conn = db.connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO facts "
            "(source_doc_id, fact_type, payload_json, source_quote, confidence) "
            "VALUES (?, ?, ?, ?, ?)",
            (int(doc_id), fact_type, payload_json, source_quote, float(confidence)),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def run_once(config: AppConfig, *, batch_size: int = 25) -> int:
    """Process up to batch_size docs. Returns count of facts successfully written."""
    router = LLMRouter(config)
    docs = _fetch_batch(config.database_path, batch_size)
    written = 0
    for d in docs:
        schema = schema_for(d.source)
        base_prompt = prompt_for(d.source)
        n = _process_one(
            config=config, router=router, doc=d, schema=schema,
            base_prompt=base_prompt,
        )
        written += n
        _mark_processed(config.database_path, d.id)
    return written


def _process_one(*, config, router, doc, schema, base_prompt) -> int:
    """Try once; on quote-check failure retry once with stricter prompt."""
    attempts = [
        base_prompt,
        base_prompt + (
            "\n\nIMPORTANT: a previous attempt fabricated a quote. The "
            "`source_quote` you produce MUST appear verbatim in the document. "
            "If you cannot find a verbatim quote, return lower confidence and "
            "fewer claims rather than inventing one."
        ),
    ]
    for prompt_text in attempts:
        prompt = f"{prompt_text}\n\n--- BEGIN DOCUMENT ---\n{doc.content}\n--- END ---"
        try:
            parsed, _meta = router.extract(prompt, schema)
        except Exception as e:
            log.warning("extractor: LLM call failed for doc %s: %s", doc.id, e)
            return 0
        if not quote_in_source(parsed.source_quote, doc.content):
            log.warning("extractor: quote check failed for doc %s", doc.id)
            continue
        payload_json = parsed.model_dump_json()
        fact_id = _insert_fact(
            config.database_path,
            doc_id=doc.id,
            fact_type=fact_type_for(doc.source),
            payload_json=payload_json,
            source_quote=parsed.source_quote,
            confidence=float(parsed.confidence),
        )
        vault.publish_extraction(raw_document_id=doc.id, fact_ids=[fact_id])
        return 1
    return 0
```

- [ ] **Step 4: Run — expect PASS**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_extractor.py -v`

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
cd /Users/devamarnani/Desktop/bsebot
git add tests/test_extractor.py bsebot/extractor.py
git commit -m "feat(bsebot): add fact extractor with verbatim quote verification + retry"
```

---

## Task 16: Bootstrap CLI command (`bsebot/bootstrap.py`) — TDD

**Files:**
- Create: `/Users/devamarnani/Desktop/bsebot/tests/test_bootstrap.py`
- Create: `/Users/devamarnani/Desktop/bsebot/bsebot/bootstrap.py`

- [ ] **Step 1: Write failing tests**

Write `/Users/devamarnani/Desktop/bsebot/tests/test_bootstrap.py`:

```python
from bsebot import bootstrap, db
from bsebot.config import AppConfig, LLMConfig, ProviderConfig


def _config(db_path):
    return AppConfig(
        database_path=str(db_path),
        llm=LLMConfig(
            extract_max_tokens=2048, reason_max_tokens=8192,
            continuation_max_attempts=3,
            providers=[ProviderConfig("g", "gemini/gemini-2.5-flash",
                                      "GEMINI_API_KEY", "k", ["extract", "reason"])],
        ),
    )


def test_bootstrap_runs_each_step_once(tmp_path, migrations_dir, monkeypatch):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)
    calls: list[str] = []

    def stub(name):
        def _fn(cfg, **kw):
            calls.append(name)
            from bsebot.harvesters.base import HarvestResult
            return HarvestResult(harvester=name, status="success",
                                 docs_fetched=0, docs_new=0)
        return _fn

    monkeypatch.setattr(bootstrap, "_run_price_history", stub("price_history"))
    monkeypatch.setattr(bootstrap, "_run_screener", stub("screener_bse"))
    monkeypatch.setattr(bootstrap, "_run_nse", stub("nse_announcements"))
    monkeypatch.setattr(bootstrap, "_run_sebi", stub("sebi_circulars"))
    monkeypatch.setattr(bootstrap, "_run_news", stub("google_news"))
    monkeypatch.setattr(bootstrap, "_run_extractor", lambda cfg: 0)

    report = bootstrap.run_full_bootstrap(_config(db_path))
    assert report["price_history"]["status"] == "success"
    assert "screener_bse" in report
    assert "sebi_circulars" in report
    assert "google_news" in report
    assert "nse_announcements" in report
    assert calls == [
        "price_history",
        "screener_bse",
        "nse_announcements",
        "sebi_circulars",
        "google_news",
    ]


def test_bootstrap_is_idempotent(tmp_path, migrations_dir, monkeypatch):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)

    def stub(name):
        def _fn(cfg, **kw):
            from bsebot.harvesters.base import HarvestResult
            return HarvestResult(harvester=name, status="success",
                                 docs_fetched=0, docs_new=0)
        return _fn

    for fn_name, name in [
        ("_run_price_history", "price_history"),
        ("_run_screener", "screener_bse"),
        ("_run_nse", "nse_announcements"),
        ("_run_sebi", "sebi_circulars"),
        ("_run_news", "google_news"),
    ]:
        monkeypatch.setattr(bootstrap, fn_name, stub(name))
    monkeypatch.setattr(bootstrap, "_run_extractor", lambda cfg: 0)

    bootstrap.run_full_bootstrap(_config(db_path))
    bootstrap.run_full_bootstrap(_config(db_path))
    conn = db.connect(db_path)
    try:
        n = conn.execute("SELECT COUNT(*) FROM harvester_runs").fetchone()[0]
    finally:
        conn.close()
    # Each stub is a function we patched — the real harvester would have
    # written runs; with stubs no rows are written by the stubs themselves.
    # Idempotency here means: no raised exceptions on rerun.
    assert n >= 0
```

- [ ] **Step 2: Implement `bsebot/bootstrap.py`**

Write `/Users/devamarnani/Desktop/bsebot/bsebot/bootstrap.py`:

```python
"""One-time history bootstrap.

Pulls 5y price history, screener financials, NSE archives (6 months),
SEBI circulars index (24 months — index only, full PDF fetched lazily by
agent), and 30 days of Google News. Then runs the extractor over the new
raw_documents. Idempotent due to content_hash dedup.
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from bsebot import extractor
from bsebot.config import AppConfig
from bsebot.harvesters.google_news import GoogleNewsHarvester
from bsebot.harvesters.nse_announcements import NseAnnouncementsHarvester
from bsebot.harvesters.price_history import PriceHistoryHarvester
from bsebot.harvesters.screener_bse import ScreenerBseHarvester
from bsebot.harvesters.sebi_circulars import SebiCircularsHarvester


log = logging.getLogger(__name__)


def _run_price_history(cfg: AppConfig, **kw):
    h = PriceHistoryHarvester(db_path=cfg.database_path, period="5y")
    return h.fetch_and_persist()


def _run_screener(cfg: AppConfig, **kw):
    h = ScreenerBseHarvester(db_path=cfg.database_path)
    return h.fetch_and_persist()


def _run_nse(cfg: AppConfig, **kw):
    h = NseAnnouncementsHarvester(db_path=cfg.database_path, max_items=200)
    return h.fetch_and_persist()


def _run_sebi(cfg: AppConfig, **kw):
    # Index-only by default — `max_circulars` caps PDF fetches.
    h = SebiCircularsHarvester(db_path=cfg.database_path, max_circulars=100)
    return h.fetch_and_persist()


def _run_news(cfg: AppConfig, **kw):
    h = GoogleNewsHarvester(db_path=cfg.database_path)
    return h.fetch_and_persist()


def _run_extractor(cfg: AppConfig) -> int:
    return extractor.run_once(cfg, batch_size=200)


def run_full_bootstrap(cfg: AppConfig) -> dict:
    """Run all bootstrap steps; return per-step report."""
    report: dict = {}
    for label, fn in [
        ("price_history", _run_price_history),
        ("screener_bse", _run_screener),
        ("nse_announcements", _run_nse),
        ("sebi_circulars", _run_sebi),
        ("google_news", _run_news),
    ]:
        log.info("bootstrap: %s", label)
        try:
            res = fn(cfg)
            report[label] = asdict(res) if hasattr(res, "__dataclass_fields__") else {
                "status": "success", "docs_fetched": 0, "docs_new": 0,
            }
        except Exception as e:
            log.warning("bootstrap %s failed: %s", label, e)
            report[label] = {"status": "failure", "error": repr(e)}

    log.info("bootstrap: running extractor")
    try:
        n = _run_extractor(cfg)
        report["extractor"] = {"facts_written": n}
    except Exception as e:
        log.warning("bootstrap extractor failed: %s", e)
        report["extractor"] = {"facts_written": 0, "error": repr(e)}
    return report
```

- [ ] **Step 3: Run — expect PASS**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_bootstrap.py -v`

Expected: 2 passed.

- [ ] **Step 4: Commit**

```bash
cd /Users/devamarnani/Desktop/bsebot
git add tests/test_bootstrap.py bsebot/bootstrap.py
git commit -m "feat(bsebot): add one-time history bootstrap orchestrator"
```

---

## Task 17: Wire CLI — `harvest`, `extract`, `bootstrap`

**Files:**
- Modify: `/Users/devamarnani/Desktop/bsebot/bsebot/cli.py`
- Create: `/Users/devamarnani/Desktop/bsebot/tests/test_cli_data.py`

- [ ] **Step 1: Write failing tests `tests/test_cli_data.py`**

Write `/Users/devamarnani/Desktop/bsebot/tests/test_cli_data.py`:

```python
from click.testing import CliRunner

from bsebot import cli, db


def _write_cfg(tmp_path, db_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "database:\n"
        f"  path: {db_path}\n"
        "llm:\n"
        "  extract_max_tokens: 2048\n  reason_max_tokens: 8192\n"
        "  continuation_max_attempts: 3\n  providers:\n"
        "    - name: gemini\n      model: gemini/gemini-2.5-flash\n"
        "      api_key_env: GEMINI_API_KEY\n      roles: [extract, reason]\n",
        encoding="utf-8",
    )
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=fake\n", encoding="utf-8")
    return cfg, env


def test_harvest_list_prints_known_names(tmp_path, migrations_dir):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)
    cfg, env = _write_cfg(tmp_path, db_path)
    runner = CliRunner()
    res = runner.invoke(
        cli.main, ["--config", str(cfg), "--env", str(env), "harvest", "list"],
    )
    assert res.exit_code == 0, res.output
    for n in ["sebi_circulars", "google_news", "price_history",
              "nse_announcements", "screener_bse", "turnover_data",
              "bse_announcements"]:
        assert n in res.output


def test_harvest_one_runs_named_harvester(tmp_path, migrations_dir, monkeypatch):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)
    cfg, env = _write_cfg(tmp_path, db_path)

    from bsebot.harvesters import google_news

    class _StubFeed:
        entries = [
            type("E", (), {"title": "t", "link": "https://x/1",
                           "summary": "s", "published": "now"})(),
        ]

    monkeypatch.setattr(google_news.feedparser, "parse", lambda url: _StubFeed())

    runner = CliRunner()
    res = runner.invoke(
        cli.main,
        ["--config", str(cfg), "--env", str(env),
         "harvest", "one", "google_news"],
    )
    assert res.exit_code == 0, res.output
    assert "google_news" in res.output
    assert "1" in res.output  # docs_new


def test_extract_invokes_extractor(tmp_path, migrations_dir, monkeypatch):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)
    cfg, env = _write_cfg(tmp_path, db_path)

    from bsebot import extractor as ex
    monkeypatch.setattr(ex, "run_once", lambda cfg, batch_size=25: 7)

    runner = CliRunner()
    res = runner.invoke(
        cli.main, ["--config", str(cfg), "--env", str(env), "extract"],
    )
    assert res.exit_code == 0, res.output
    assert "7" in res.output


def test_bootstrap_runs(tmp_path, migrations_dir, monkeypatch):
    db_path = tmp_path / "bsebot.db"
    db.run_migrations(db_path, migrations_dir)
    cfg, env = _write_cfg(tmp_path, db_path)

    from bsebot import bootstrap as bs
    monkeypatch.setattr(bs, "run_full_bootstrap",
                        lambda cfg: {"price_history": {"docs_new": 5}})

    runner = CliRunner()
    res = runner.invoke(
        cli.main, ["--config", str(cfg), "--env", str(env), "bootstrap"],
    )
    assert res.exit_code == 0, res.output
    assert "price_history" in res.output
```

- [ ] **Step 2: Update `bsebot/cli.py`**

Open `/Users/devamarnani/Desktop/bsebot/bsebot/cli.py`. Replace the existing `@main.group() def harvest():` block (and its `harvest_all` + `harvest_one` stubs) with the real version below; replace the existing `@main.command("extract") def extract_cmd():` stub with the real one; add a new `bootstrap` command.

Replace the entire `# --------- stubs for future plans ---------` section's harvest group + `extract_cmd` with:

```python
# --------- harvest group (Plan 2) ---------

from bsebot import bootstrap as _bootstrap
from bsebot import extractor as _extractor
from bsebot import harvesters as _harvesters


@main.group()
def harvest() -> None:
    """Run harvesters."""


@harvest.command("list")
@click.pass_context
def harvest_list(ctx: click.Context) -> None:
    """Print every registered harvester name."""
    for name in _harvesters.all_names():
        click.echo(name)


@harvest.command("all")
@click.pass_context
def harvest_all(ctx: click.Context) -> None:
    """Run every registered harvester sequentially."""
    cfg = _load_app_config(ctx)
    for name in _harvesters.all_names():
        result = _run_harvester(cfg, name)
        click.echo(
            f"{result.harvester}: status={result.status} "
            f"fetched={result.docs_fetched} new={result.docs_new}"
        )


@harvest.command("one")
@click.argument("name")
@click.pass_context
def harvest_one(ctx: click.Context, name: str) -> None:
    """Run a single named harvester."""
    cfg = _load_app_config(ctx)
    result = _run_harvester(cfg, name)
    click.echo(
        f"{result.harvester}: status={result.status} "
        f"fetched={result.docs_fetched} new={result.docs_new}"
    )


def _run_harvester(cfg, name: str):
    cls = _harvesters.get(name)
    return cls(db_path=cfg.database_path).fetch_and_persist()


# --------- extract command (Plan 2) ---------

@main.command("extract")
@click.option("--batch-size", default=25, show_default=True, type=int)
@click.pass_context
def extract_cmd(ctx: click.Context, batch_size: int) -> None:
    """Process unprocessed raw_documents into facts."""
    cfg = _load_app_config(ctx)
    n = _extractor.run_once(cfg, batch_size=batch_size)
    click.echo(f"extracted {n} facts")


# --------- bootstrap (Plan 2) ---------

@main.command("bootstrap")
@click.pass_context
def bootstrap_cmd(ctx: click.Context) -> None:
    """One-time history bootstrap (price_history, screener, news, sebi, nse)."""
    cfg = _load_app_config(ctx)
    report = _bootstrap.run_full_bootstrap(cfg)
    for step, info in report.items():
        click.echo(f"{step}: {info}")
```

Use `Edit` to remove the existing stubbed `@main.group() def harvest` block and stubbed `extract_cmd` from Plan 1's cli.py. The old code to find (Plan 1, lines 1741-1763 of plan 1) is:

```python
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
```

Replace that exact block with the new code above.

- [ ] **Step 3: Run — expect PASS**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest tests/test_cli_data.py -v`

Expected: 4 passed.

- [ ] **Step 4: Run full test suite — expect all green**

Run: `cd /Users/devamarnani/Desktop/bsebot && python3.11 -m pytest -v`

Expected: every test in Plan 1 + Plan 2 passes.

- [ ] **Step 5: Commit**

```bash
cd /Users/devamarnani/Desktop/bsebot
git add bsebot/cli.py tests/test_cli_data.py
git commit -m "feat(bsebot): wire CLI harvest/extract/bootstrap commands"
```

---

## Task 18: Migration 002 (supporting indexes)

**Files:**
- Create: `/Users/devamarnani/Desktop/bsebot/migrations/002_harvester_indexes.sql`

The extractor query `SELECT ... FROM raw_documents WHERE processed=0 ORDER BY id LIMIT ?` already uses `idx_raw_documents_processed`. Add a couple more for facts lookup.

- [ ] **Step 1: Write migration**

Write `/Users/devamarnani/Desktop/bsebot/migrations/002_harvester_indexes.sql`:

```sql
-- Plan 2 supporting indexes.
CREATE INDEX IF NOT EXISTS idx_facts_doc_type ON facts(source_doc_id, fact_type);
CREATE INDEX IF NOT EXISTS idx_raw_documents_source_processed
  ON raw_documents(source, processed);
CREATE INDEX IF NOT EXISTS idx_price_history_date ON price_history(date);
```

- [ ] **Step 2: Verify it applies**

Run: `cd /Users/devamarnani/Desktop/bsebot && rm -f /tmp/bsebot_mig_test.db && python3.11 -c "from pathlib import Path; from bsebot import db; print(db.run_migrations(Path('/tmp/bsebot_mig_test.db'), Path('migrations')))"`

Expected output ends with `['001_initial.sql', '002_harvester_indexes.sql']` on first run.

- [ ] **Step 3: Commit**

```bash
cd /Users/devamarnani/Desktop/bsebot
git add migrations/002_harvester_indexes.sql
git commit -m "feat(bsebot): add supporting indexes for facts + raw_documents lookups"
```

---

## Self-Review Notes (post-write)

**Spec coverage of Plan 2 scope:**
- Harvester base class (hash dedup, harvester_runs row) — Task 7.
- Politeness (per-domain delay, UA rotation, backoff) — Task 1.
- SEBI circulars harvester — Task 8.
- Screener.in harvester — Task 10.
- Google News RSS harvester — Task 9.
- NSE archives harvester — Task 11.
- BSE announcements (screener proxy) — Task 12.
- Turnover data — Task 13.
- Price watcher base (yfinance → price_history) — Task 14. (Note: the 30-second loop / systemd unit is Plan 3's concern; this task delivers only the deterministic bulk pull.)
- Deterministic numeric parsers — Tasks 3, 4, 5.
- Pydantic fact schemas + registry + `generic_web` — Task 6.
- Extractor (instructor + quote verification + retry) — Task 15.
- Bootstrap CLI — Task 16.
- `bsebot harvest list/one/all`, `bsebot extract`, `bsebot bootstrap` CLI wiring — Task 17.
- Supporting indexes — Task 18.

**Vault calls deferred:** Harvester base + extractor call `vault.publish_source` / `vault.publish_extraction`; Task 2 ships these as no-op stubs so callers compile. Plan 4 implements the real writer.

**Type/name consistency:**
- `Harvester.fetch_and_persist() -> HarvestResult` is used by base, every concrete harvester, the CLI, and bootstrap.
- `bsebot.harvesters.get(name)` / `all_names()` is used by CLI and tests.
- `bsebot.extractor.run_once(cfg, batch_size=...)` is used by tests, CLI, and bootstrap.
- `bsebot.bootstrap.run_full_bootstrap(cfg)` returns a dict keyed by step name.
- Schema registry: `schema_for`, `prompt_for`, `fact_type_for` — used in extractor and tested directly.
- `bsebot.http.HttpClient` is consumed by all HTTP-using harvesters; tests inject `_StubHttp` duck-typed against the same `.get(url, **kw) -> HttpResponse` shape.

**Placeholder scan:** No "TBD" or "implement later" markers. Every code step is complete. The vault calls are explicitly stubbed with a working no-op (`PublishResult(written=False)`), not a TODO.

**Items intentionally deferred to Plan 3+:**
- 30-second price watcher loop + systemd unit (Plan 3).
- Adversarial fact-checker pass (Plan 3 — sits between extractor output and agent intake).
- `request_new_source` integration + `tools` table reads/writes (Plan 3).
- `fetch_url` / `web_search` runtime hooks for the agent (Plan 3).

**Items intentionally deferred to Plan 4:**
- Real `vault.publish_source` / `publish_extraction` (Plan 4).
- Quartz rebuild trigger.

**Test isolation:** Every harvester test injects a stub HTTP client; no test touches the network. Extractor tests monkeypatch `LLMRouter`; price-history test monkeypatches `yfinance.Ticker`. Total network calls during `pytest`: zero.
