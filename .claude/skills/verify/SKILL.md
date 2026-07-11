---
name: verify
description: How to run and visually verify meshapp UI changes locally (no radio needed)
---

# Verifying meshapp changes locally

The serial radio is absent locally, but the Flask web UI and SQLite work fine.

## Launch

1. Copy the reference prod DB into the working dir if it isn't there:
   `cp <repo-root>/meshapp.db ./meshapp.db` (gitignored).
2. `PORT=5199 uv run main.py` (background). It logs serial-connection
   exceptions — ignore them; the web server still comes up.
3. `http://127.0.0.1:5199/` redirects to `/messages/unknown`, the channel
   with the most data (~2000 texts, ~460 tapbacks in the reference DB).

## Drive

No browser tooling in the repo — install Playwright in a throwaway venv:
`uv venv <tmp>/pwenv && uv pip install playwright --python <tmp>/pwenv`
then `python -m playwright install chromium`.

- CLAUDE.md requires checking desktop **and** mobile: use viewports
  1280x900 and 390x844; dark/light via `color_scheme`.
- Wait for `.msg-node` before measuring/screenshotting.
- Useful selectors: `.bubble--clickable` (opens message modal on click),
  `.tapback-row` / `.tapback` (reaction pills, click opens modal),
  `.node-name` (styled inline from JS `nameStyle()` in
  `templates/messages.html`), `.bubble--placeholder` (not clickable).

## Gotchas

- Windows: the sqlite file stays locked while the server runs; stop the
  listener on port 5199 (`Get-NetTCPConnection -LocalPort 5199 -State Listen`)
  before deleting the DB copy.
- Message-list CSS lives in `static/style.css`; the message rendering JS is
  inline in `templates/messages.html`, not `static/app.js`.
