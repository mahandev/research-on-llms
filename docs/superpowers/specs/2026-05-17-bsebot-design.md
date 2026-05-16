# BSEBot — Autonomous Paper Trading Bot for BSE Ltd

**Date:** 2026-05-17
**Status:** Design locked, ready for implementation planning
**Owner:** Dev

## Goal

Build an autonomous paper-trading bot focused on a single stock (BSE Ltd, NSE ticker `BSE`). The bot:

- Harvests information from public sources (SEBI, NSE archives, screener.in, Google News, BSE announcements)
- Extracts facts using a cheap LLM with strict grounding
- Runs an autonomous decision agent (memory, tools, alerts) that picks holding period per trade
- Executes simulated trades from a ₹10,000 starting capital with ₹10/day operating overhead
- Publishes a private Obsidian-style knowledge base + live P&L dashboard via Quartz, served behind Cloudflare Tunnel on Dev's `.in` domain

The user is a software engineer, not a stocks expert. The bot must produce grounded, citable reasoning that the user can audit. Hallucinations are unacceptable.

## Architecture overview

```
┌─────────────────────── LENOVOSERVER (always on) ───────────────────────┐
│                                                                        │
│   ┌─────────────┐    ┌──────────────┐    ┌──────────────────┐         │
│   │ Harvesters  │───>│ SQLite (WAL) │<───│ Extractor        │         │
│   │ (cron)      │    │              │    │ (cron, processes │         │
│   └─────────────┘    │  raw_docs    │    │  unprocessed)    │         │
│                      │  facts       │    └──────────────────┘         │
│   ┌─────────────┐    │  agent_runs  │              ▲                  │
│   │ Price       │───>│  alerts      │              │                  │
│   │ watcher     │    │  trades      │              │  LLM calls       │
│   │ (systemd,   │    │  memory      │              │                  │
│   │  30s loop)  │    └──────────────┘    ┌─────────┴────────┐         │
│   └──────┬──────┘            ▲           │ LLM Router       │         │
│          │ fires              │           │ (litellm wrap)   │         │
│          ▼                    │           │ Gemini → Cerebras│         │
│   ┌─────────────┐             │           │ → Groq → GitHub  │         │
│   │ Agent       │─────────────┘           └──────────────────┘         │
│   │ runner      │             ▲                    ▲                  │
│   │ (event-     │─────────────┘                    │                  │
│   │  driven +   │                                   │                  │
│   │  4pm cron)  │                                   │                  │
│   └──────┬──────┘                                   │                  │
│          │                                          │                  │
│          ▼                                          │                  │
│   ┌─────────────┐    ┌──────────────┐    ┌────────────────────┐       │
│   │ Vault       │───>│ Markdown     │───>│ Quartz static site │       │
│   │ writer      │    │ files +      │    │ (rebuild on change)│       │
│   └─────────────┘    │ frontmatter  │    └──────────┬─────────┘       │
│                      └──────────────┘               │                  │
│                                                     ▼                  │
│                                            ┌────────────────┐         │
│                                            │ cloudflared    │         │
│                                            │ tunnel         │         │
│                                            └────────┬───────┘         │
└─────────────────────────────────────────────────────┼─────────────────┘
                                                      │
                                                      ▼
                                      bsebot.<newdomain>.in
                                      (private, Cloudflare Access)
```

## Components

### 1. Harvesters (`bsebot/harvesters/`)

Independent CLI-callable scripts. Each one:

- Fetches from one source
- Computes content hash; skips if already in `raw_documents`
- Writes raw text to `raw_documents` with `processed=0`
- Writes a `harvester_runs` row (success/failure/partial)
- Calls vault writer to publish a Source markdown file

Harvesters to build in order:

| Harvester | Source | Cadence | Notes |
|---|---|---|---|
| `sebi_circulars` | sebi.gov.in HTML + PDFs | daily 6pm | most-impact source for derivatives regs |
| `screener_bse` | screener.in/company/BSE/consolidated/ | 2x/day | quarterly P&L, shareholding, announcements |
| `google_news` | Google News RSS, query "BSE Ltd" | every 30 min | feedparser, no auth |
| `nse_announcements` | nsearchives.nseindia.com archive PDFs | daily 8pm | archive subdomain bypasses bot block |
| `bse_announcements` | via screener.in proxy links to NSE archives | daily 7pm | bseindia.com blocks bots |
| `turnover_data` | way2wealth.com/market/volumeturnover | every 15 min during market hours | deterministic table parse |
| `price_watcher` | yfinance `BSE.NS` | every 30s during market hours | systemd service, not cron; also fires alerts |

**Reddit is excluded as a fact source** (ungroundable). If wanted later, builds a single aggregate sentiment score, not citable facts.

**Politeness:** min 2s delay between requests to same domain, exponential backoff, User-Agent rotation.

### 2. Fact extractor (`bsebot/extractor.py`)

- Polls `raw_documents WHERE processed=0`
- Routes each doc to a source-specific Pydantic schema (see Grounding section)
- Uses **`instructor`** library wrapping the LLM router → structured output with auto-retry on parse failure
- Two-stage check before saving each fact:
  1. **Schema validation** (Pydantic)
  2. **Quote verification** — every fact has a `source_quote` field; we substring-check it appears in the source text. Failure → discard fact, log warning, retry once with stricter prompt.
- **Numbers are NOT extracted by LLM.** Prices, volumes, P/E, EPS, turnover all come from deterministic HTML/regex parsers in `bsebot/parsers/`. LLM only narrates.
- Marks `raw_documents.processed=1` after, even if some facts failed (logs the failures).
- Calls vault writer.

### 3. LLM router (`bsebot/llm.py`)

Wraps `litellm` so all model calls go through one interface. Two roles:

- `extract(prompt, schema)` — cheap structured output (via `instructor`)
- `reason(messages, tools)` — heavy reasoning with tool calls

**Provider chain (config.yaml-driven, swappable):**

| Priority | Provider | Model | Used for | Why |
|---|---|---|---|---|
| 1 | Google AI Studio | `gemini-2.5-flash` | extract + reason | 1500 req/day free, 1M context, 64K output; paid spillover from user's billing |
| 2 | Cerebras | `llama-3.3-70b` or similar | extract (fallback) | 1M tokens/day free, 30 req/min, 8K context cap (fine for extraction) |
| 3 | Groq | `llama-3.3-70b-versatile` | reason (fallback) | Fast, 14K req/day cap |
| 4 | GitHub Models | `claude-3.5-sonnet` or `gpt-4.1` | weekly portfolio review only | Best quality, tight rate limits, 1 call/week |

**Truncation handling:**
- `max_tokens` set explicitly: 2048 (extract), 8192 (reason)
- After every call: check `finish_reason`. If `"length"` → continuation pattern (up to 3 attempts), then switch to next provider in chain.
- `instructor` auto-retries parse failures.

**Logging:** every call logs `provider, model, input_tokens, output_tokens, finish_reason, duration, continuation_count, cost_estimate` to `llm_call_log` table. Weekly report flags truncation rate > 5%.

### 4. Agent runner (`bsebot/agent.py`)

The brain. Runs in two modes:

- **Scheduled**: cron at 4:00 PM IST weekdays (after market close)
- **Triggered**: invoked by price watcher when an alert fires

**Agent loop:**
1. Acquire SQLite advisory lock on `agent_run` (concurrent fires queue)
2. Build initial context: handoff.md, active memories (curated by importance), open positions, new facts since last run, the triggering alert if any
3. LLM runs in tool-call loop, max 15 iterations
4. Must end by calling `submit_decision` tool
5. Persist: decision row, new/superseded memories, new alerts, updated handoff.md
6. Vault writer publishes decision markdown
7. Release lock

**Tools the agent can call:**

| Tool | Purpose |
|---|---|
| `query_facts(ticker, fact_type, date_from, date_to, limit)` | Structured fact lookup |
| `read_raw_document(doc_id)` | Read full source text |
| `read_memory(memory_type, limit)` | Past theses/observations/lessons |
| `write_memory(memory_type, content, importance, source_fact_ids)` | Save new memory (requires citations) |
| `supersede_memory(old_id, new_content)` | Mark old belief outdated |
| `check_open_positions()` | Current paper trades |
| `check_recent_decisions(n)` | Last N decisions + outcomes |
| `set_alert(condition, threshold, valid_until, why_this_threshold, source_fact_ids, intraday=False)` | Wake-up trigger (see Alerts section) |
| `cancel_alert(alert_id)` | Remove a stale alert |
| `request_new_source(name, description, url, rationale)` | Queue for human approval |
| `web_search(query)` | Search web; results stored as raw_doc + re-extracted before agent sees them |
| `fetch_url(url)` | Fetch URL; result stored as raw_doc + re-extracted |
| `get_current_price()` | Latest known price + timestamp |
| `submit_decision(action, confidence, reasoning, fact_ids_consulted, stop_loss, target, quantity)` | Terminate run with final decision |

**`submit_decision` validation:**
- `reasoning` must contain `[fact:NNNN]` references for every claim — a regex pre-check rejects unsupported claims
- Adversarial fact-checker pass: second LLM call with prompt `"You are a skeptical auditor. For each claim in the reasoning, does the cited fact actually support it? Reply YES or NO with explanation."` — any NO blocks execution; decision is logged but no trade placed

### 5. Alert system (`bsebot/alerts.py`)

Stored in SQLite `alerts` table. Created by agent via `set_alert` tool; checked by price watcher.

**Schema:**
```sql
CREATE TABLE alerts (
  id INTEGER PRIMARY KEY,
  created_by_agent_run INTEGER REFERENCES agent_runs(id),
  condition TEXT NOT NULL,  -- 'price_above'|'price_below'|'price_change_pct'|'volume_spike_x'|'news_keyword'|'time_elapsed'
  ticker TEXT NOT NULL,
  threshold REAL NOT NULL,
  valid_until TIMESTAMP NOT NULL,
  why_this_threshold TEXT NOT NULL,
  source_fact_ids TEXT NOT NULL,  -- JSON array
  linked_trade_id INTEGER REFERENCES trades(id),
  linked_thesis_id INTEGER REFERENCES agent_memory(id),
  intraday INTEGER DEFAULT 0,
  active INTEGER DEFAULT 1,
  fired_at TIMESTAMP,
  cooldown_until TIMESTAMP,
  fire_count INTEGER DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Rules enforced at creation (`set_alert` tool):**

| Rule | Value |
|---|---|
| Max active alerts | 8 |
| Max new alerts created per day | 5 |
| `valid_until` required, max | 30 days from now |
| Threshold must be ≥1% from current price | enforced |
| Threshold must be ≥1% from other active alerts (same condition) | enforced (dedup) |
| `why_this_threshold` text required | enforced |
| `source_fact_ids` array, ≥1 fact required | enforced |
| Auto-deactivate when `linked_trade_id` closes | trigger on `trades` table update |
| Auto-deactivate when `linked_thesis_id` superseded | trigger on `agent_memory` table update |

**Rules enforced at firing (price watcher):**

| Rule | Value |
|---|---|
| Per-alert cooldown after firing | 60 min (default) / 5 min (intraday) |
| Max triggered agent runs per day | 5 |
| Scheduled run quota always reserved | 1 daily run unblockable |
| Concurrent fires consolidate | If agent already running, queue + combine context into next run |

**Agent's prompt always includes live alert budget status:**
```
ALERT BUDGET:
- Active: 4/8
- Created today: 3/5
- Triggered runs today: 1/5
- Scheduled-run reserve: available
```

### 6. Position manager (`bsebot/position_manager.py`)

Called by price watcher on each tick when open positions exist (cheap, no LLM).

- For each open trade, compute unrealized P&L from current price
- If stop_loss or target hit → close trade, log exit_reason, vault-publish trade summary
- If trade has expired (e.g., `force_exit_by` timestamp passed) → close at market

Also runs the **daily ₹10 overhead deduction** at midnight IST via cron — debits a row in the `cash_ledger` table (separate from `trades`).

**Cash accounting:** `cash_ledger` table records every cash movement (starting deposit, trade entry, trade exit, daily overhead). Current cash balance = `SUM(cash_ledger.amount)`. P&L = current cash balance + mark-to-market value of open positions − starting capital.

### 7. Vault writer (`bsebot/vault.py`)

Writes Obsidian-style markdown to `/opt/bsebot/vault/`. Every file has YAML frontmatter with `type`, `date`, `tags`, `related` wikilinks.

**Vault structure:**
```
vault/
├── 00_Dashboard.md            # auto, links to everything
├── 01_Active_Thesis.md        # current view on BSE
├── 02_Open_Positions.md       # live, regenerated on every trade update
├── 03_PnL.md                  # P&L + embedded PNG chart; regenerated on every trade event + every 5 min during market hours (inotify rebuilds Quartz → site shows fresh numbers)
├── Daily_Logs/YYYY-MM-DD.md
├── Decisions/YYYY-MM-DD_HHMM_<action>.md
├── Sources/{SEBI,News,Screener,Announcements,Web}/<file>.md
├── Memory/{Theses,Observations,Lessons}/<id>_<slug>.md
├── Trades/trade_<id>.md
├── Alerts/active.md           # live snapshot of all active alerts
└── _internal/handoff.md       # latest agent handoff (overwritten each run)
```

**Wikilinks** are auto-generated:
- Every decision file links to every source document it cited
- Every source file is backlinked from the day's Daily_Log
- Every trade links to the decision that opened it + the alerts attached to it

### 8. Quartz site

- Quartz 4 installed in `/opt/bsebot/quartz/`
- Symlinks `/opt/bsebot/vault/` → `/opt/bsebot/quartz/content/`
- Rebuild triggered by inotify watcher on vault changes (debounced 30s)
- Served on `localhost:8080` via `npx quartz build --serve`
- Exposed via Cloudflare Tunnel as `bsebot.<newdomain>.in` with **Cloudflare Access policy** requiring Dev's email login

### 9. CLI (`bsebot/cli.py`)

```
bsebot harvest <name>                  # run one harvester
bsebot harvest all                     # all enabled
bsebot extract                         # process unprocessed docs
bsebot agent run [--triggered-by ID]   # run agent (scheduled or triggered)
bsebot positions check                 # update P&L, check stops/targets
bsebot positions list
bsebot trades history [--days N]
bsebot alerts list
bsebot alerts cancel <id>
bsebot vault rebuild                   # regenerate all markdown from DB
bsebot vault publish                   # quartz build
bsebot vault serve                     # quartz serve on :8080
bsebot llm test                        # ping every configured provider
bsebot db query "<sql>"                # read-only ad-hoc
bsebot db stats                        # row counts
bsebot report daily                    # generate end-of-day summary
bsebot tools list                      # registered sources
bsebot tools approve <id>              # approve agent-requested source
```

## Database schema

(Full DDL in implementation, summarized here.)

| Table | Purpose |
|---|---|
| `raw_documents` | Append-only fetch log with content_hash dedup |
| `facts` | Structured extractions with `source_doc_id`, `source_quote`, confidence |
| `agent_runs` | Every agent invocation: decision, reasoning, fact_ids consulted, tools called, model, cost |
| `trades` | Paper trade ledger: entry/exit, stop/target, P&L, status |
| `agent_memory` | Theses, observations, lessons, with supersedence chain |
| `alerts` | (see Alert system above) |
| `tools` | Registry of agent-requested sources awaiting approval |
| `harvester_runs` | Status row per harvester invocation |
| `llm_call_log` | Per-call: provider, model, tokens, finish_reason, cost |
| `cash_ledger` | All cash movements (deposit, trade entry/exit, daily ₹10 overhead) |
| `price_history` | Deterministic OHLCV time series (from yfinance) |

WAL mode, foreign keys on, indexes on hot lookups.

## Grounding — hard requirements

The non-negotiable rules to prevent hallucination:

1. **Verbatim quote requirement.** Every fact has a `source_quote` field; substring-verified against source text before save.
2. **Numbers come from deterministic parsers.** LLM never extracts a price, ratio, or volume.
3. **Fact-ID citations.** Agent reasoning must reference `[fact:NNNN]` for every claim; regex check rejects unsupported claims; vault renders these as clickable backlinks.
4. **No pre-training knowledge.** System prompt: *"You have zero prior knowledge of BSE Ltd, Indian markets, or finance. Your only information is the facts and documents provided. If you need something missing, use `request_new_source` or `web_search`. Do not recall from training."*
5. **Adversarial fact-checker pass.** Second LLM call audits every claim against its cited fact; mismatches block execution.
6. **Web/search results pipelined.** Anything fetched via `web_search` or `fetch_url` is stored as a `raw_document` first, extracted to facts second, read by agent third. No "I searched and now know X."
7. **Memory provenance.** Every `agent_memory` row must list `source_fact_ids` it's derived from. Memories without provenance are rejected.

## Deployment topology

**Host:** lenovoserver (Ubuntu 24.04, i3-1115G4, 7.6 GB RAM, 4 GB swap, ~135 GB unallocated LVM).

**Layout:**
```
/opt/bsebot/
├── .venv/
├── .env                       # API keys
├── config.yaml                # models, thresholds, cadence
├── data/bsebot.db
├── logs/                      # rotated weekly
├── vault/                     # markdown knowledge base
├── quartz/                    # node project, builds vault → static site
├── bsebot/                    # python package
├── scripts/setup.sh
└── systemd/                   # unit files for price_watcher, quartz_server
```

**Processes:**
- **cron**: harvesters, extractor, scheduled agent run (4pm), daily report, daily ₹10 overhead deduction, vault rebuild
- **systemd unit `bsebot-price-watcher.service`**: 30s loop during market hours, polls yfinance, evaluates alerts, fires agent
- **systemd unit `bsebot-quartz.service`**: `npx quartz build --serve` on :8080
- **inotify on vault/**: triggers debounced Quartz rebuild

**Network:**
- Add `bsebot.<newdomain>.in → localhost:8080` to lenovoserver cloudflared config (after `.in` domain purchase + DNS setup)
- Cloudflare Access policy: only `ai@fortheye.co` or specified emails allowed

**Setup script** (`scripts/setup.sh`):
1. Verify Python 3.11+
2. Create venv at `/opt/bsebot/.venv`
3. Install deps from `pyproject.toml`
4. Run migrations
5. Verify `.env` has GEMINI_API_KEY, CEREBRAS_API_KEY, GROQ_API_KEY, GITHUB_MODELS_TOKEN
6. `bsebot llm test` — ping every configured provider
7. Install systemd units, enable timers
8. Install Node.js for Quartz, run `npx quartz` init
9. Print cron suggestions and `cloudflared` config snippet to add

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Free LLM rate limits exhaust mid-day | Provider chain fallback; reserved quota for scheduled run |
| Hallucination | 7 grounding rules above |
| Lenovoserver shutdowns | **FIXED**: logind drop-in + masked sleep targets (2026-05-17) |
| BSE Ltd scraping blocks | NSE archive subdomains as proxy; screener.in as fallback |
| Agent runs concurrently | SQLite advisory lock + queue consolidation |
| Alert spam / oscillation | 8 active cap + 1% distance rules + per-alert cooldown |
| Stale memory pollution | Importance scoring + agent reviews 3 oldest each run |
| Domain not yet purchased | Initial deployment can use existing `bsebot.roadto405.xyz` subdomain until `.in` domain is live |
| Lenovo offline | Watchdog re-runs on reboot via systemd; cron picks up missed slots; no data loss (append-only DB) |

## Out of scope (explicitly not building)

- Real-money execution (paper only, forever, until separate decision)
- Multi-stock support (hardcode BSE; structure code so extending is trivial)
- Options / F&O trading logic
- Reddit as a fact source
- GUI / dashboard beyond Quartz site
- Mobile app
- ML models trained on price data
- Multi-user / shared deployment
- Backtesting framework (live forward-test only)

## Initial history bootstrap

Without seed data the agent starts blind. A one-time `bsebot bootstrap` CLI command (run once during `setup.sh`) populates the DB with historical context:

| Source | Range | Storage |
|---|---|---|
| `yfinance` price + volume history | last 5 years daily OHLCV for `BSE.NS` | `price_history` table (deterministic, no LLM extraction) |
| Screener.in financials snapshot | current page: 10y annual P&L, 10q quarterly P&L, shareholding pattern, ratios | `raw_documents` + extracted to `facts` |
| Screener.in announcements page | all visible announcements (typically last 6 months) | `raw_documents` + extracted |
| SEBI circulars index | last 24 months of circulars (titles + dated, full text fetched lazily on-demand) | `raw_documents` (index only initially) |
| NSE archives | last 6 months of announcements PDFs linked from screener.in | `raw_documents` + extracted |
| Concall transcripts | last 4 quarters via screener.in concall links | `raw_documents` + extracted |
| Google News | last 30 days RSS replay | `raw_documents` + extracted |

After bootstrap, the agent's first scheduled run reads ~500–2000 facts as context — enough to write a meaningful first thesis. Bootstrap is idempotent (content_hash dedup), so re-running is safe.

`price_history` table is separate from `facts`: deterministic OHLCV doesn't go through LLM extraction. Stored as `(date, open, high, low, close, volume)` rows. Available to the agent via a `get_price_history(date_from, date_to)` tool.

## Custom URL handling for the agent

When the agent needs information from a URL we don't have a dedicated harvester for, there are three paths depending on intent:

### Path 1: One-off fetch (`fetch_url` tool)

Agent calls `fetch_url(url)` during a run. Flow:
1. URL is fetched (requests → httpx fallback → Playwright as last resort if `playwright` is enabled)
2. Content stored as a `raw_document` with `source='agent_fetched'` and `metadata.fetched_by_agent_run=<id>`
3. Extractor immediately processes it using a **generic_web schema** (see below) — runs synchronously since the agent is waiting
4. Resulting facts are returned to the agent within the same run

### Path 2: One-off search (`web_search` tool)

Agent calls `web_search(query)`. Flow:
1. Free search API used (recommended: **Brave Search API**, 2000 queries/month free; fallback DuckDuckGo via `duckduckgo-search` library, no key)
2. Top 5 result URLs each fetched (Path 1 flow per result)
3. Aggregated facts returned to agent

### Path 3: New recurring source (`request_new_source` tool)

Agent wants a URL added as a permanent harvester (e.g., "track this newly-discovered SEBI sub-page weekly"). Flow:
1. Agent calls `request_new_source(name, description, url, fetch_method, expected_signal_type, rationale)`
2. Row inserted in `tools` table with `enabled=0, approved_at=NULL, created_by='agent'`
3. Vault writes a markdown file in `Sources/_Pending/` with the request
4. User runs `bsebot tools approve <id>` after review
5. If the URL matches an existing harvester pattern (e.g., SEBI, NSE, screener domain), an existing harvester is reused with a new config row; otherwise a **generic URL harvester** runs the fetch on a configurable cron (default daily) and routes content through the generic_web schema

### The `generic_web` extraction schema

When no source-specific schema applies, the extractor uses a looser schema that still enforces grounding:

```python
class GenericWebFact(BaseModel):
    summary: str                              # 2-3 sentence summary
    is_about_bse_ltd: bool                    # filter junk
    event_type: Literal["earnings","regulatory","product","management","macro","sentiment","other"]
    sentiment: float                          # -1.0 to 1.0
    key_claims: list[ClaimWithQuote]          # each has text + source_quote (verbatim)
    mentioned_entities: list[str]             # competitors, regulators, etc
    confidence: float                         # 0.0 to 1.0, lowered for ambiguous content

class ClaimWithQuote(BaseModel):
    claim: str                                # what the source claims
    source_quote: str                         # verbatim text from page (substring-verified)
```

Grounding rules still apply: every claim has a verbatim quote, quote is substring-checked, no numbers from LLM (deterministic parsers attempted first for any `<table>` content; if numbers found in narrative only, flagged `low_confidence`).

## Tech stack

- **Python 3.11+**
- **SQLite** (WAL mode)
- **requests + httpx** (httpx fallback for stubborn sites)
- **beautifulsoup4 + lxml** (HTML parsing)
- **pdfplumber** (SEBI PDF extraction)
- **feedparser** (RSS)
- **yfinance** (price feed)
- **pydantic + instructor** (structured LLM outputs)
- **litellm** (provider abstraction)
- **matplotlib** (P&L charts as PNG embedded in vault)
- **python-frontmatter** (YAML markdown)
- **APScheduler** (cron alternative if needed)
- **Quartz 4** (Obsidian → static site, Node-based)

## Success criteria

The bot is "working" when, for at least 7 consecutive days:

1. All cron'd harvesters complete successfully (≥95% rate)
2. All extracted facts pass quote verification (≥99% rate)
3. Agent runs daily at 4pm without intervention
4. Every decision in `Decisions/` has at least one cited `[fact:NNNN]` per non-trivial claim
5. Fact-checker pass blocks zero or near-zero decisions (high quality reasoning)
6. Quartz site is reachable at `bsebot.<domain>.in` with up-to-date P&L, decisions, sources
7. No LLM truncation events go un-recovered
8. SQLite has no orphan rows or constraint violations

## Build order

1. Project skeleton (`pyproject.toml`, `db.py`, migrations, `setup.sh`)
2. LLM router (`llm.py`) + provider chain + truncation handling + `bsebot llm test`
3. Harvester base class + `sebi_circulars` harvester end-to-end
4. Extractor (Pydantic schemas + instructor + quote verification + deterministic numeric parsers)
5. CLI scaffold (`bsebot harvest`, `bsebot extract`)
6. Vault writer + Daily_Logs + Sources publication
7. Agent runner — read-only mode first (no trade execution, just decisions logged)
8. Alert system + price watcher (systemd)
9. Adversarial fact-checker pass
10. Trade execution + position manager + ₹10/day overhead
11. Reporter + P&L chart generation
12. Quartz integration + cloudflared subdomain
13. Remaining harvesters (screener, news, NSE archives, turnover)
14. Tests + monitoring + first 7-day live run

Each step ships independently runnable.
