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
