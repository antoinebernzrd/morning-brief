# Morning Brief

A personal news dashboard, rebuilt every two hours and published as a static page.

**Live:** https://antoinebernzrd.github.io/morning-brief/

## What it does

`news_fetcher.py` pulls from RSS feeds, Telegram channels, Polymarket and price APIs, then
dedups, groups and ranks the stories and writes a single self-contained `index.html`
(installable as a PWA). Sections: tech, macro and markets, geopolitics with a conflict map,
culture, gossip, Paris and other cities, sports, and an events calendar. OpenAI is used
only to write short headlines for story groups, and results are cached.

## How it runs

```
GitHub Actions (cron, every 2h)
  -> pip install -r requirements.txt
  -> python news_fetcher.py        # fetch, dedup, render index.html
  -> commit index.html             # GitHub Pages serves it
```

Workflow: [`.github/workflows/update.yml`](.github/workflows/update.yml). It can also be
triggered by hand from the Actions tab.

## Run it locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY=...          # optional, headlines fall back without it
python news_fetcher.py             # writes index.html
```

Telegram channels are optional. Run `telegram_auth.py` once with `TELEGRAM_API_ID` and
`TELEGRAM_API_HASH` set; the session is stored in `~/.config/morning-brief/`, outside the repo.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

They run offline and in CI on every push.

## Secrets

Nothing sensitive is committed. Keys come from environment variables or git-ignored files.
Secret scanning runs locally and in CI:

```bash
brew install gitleaks
git config core.hooksPath .githooks   # enable the pre-commit hook
```

## Layout

| Path | Purpose |
|------|---------|
| `news_fetcher.py` | Fetching, dedup, ranking and HTML rendering |
| `sources.py` | Source lists, keyword sets, per-source rules (pure config) |
| `assets/` | `brief.css` and `phone_app.html`, inlined into the page at build |
| `tests/` | pytest suite for the dedup, date and filter logic |
| `scripts/` | Manual diagnostics (e.g. which FT feeds still work) |
| `telegram_auth.py` | One-time Telegram login |
| `index.html` | Generated output (committed by the bot) |
| `manifest.webmanifest`, `icons/` | PWA assets |
| `.githooks/`, `.gitleaks.toml` | Secret scanning |
