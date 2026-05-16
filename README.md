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
