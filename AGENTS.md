# Agent instructions: run and test this prototype

You are helping a reviewer run a job-interview demo: a due-diligence tool that turns a target company's tool list into an integration plan. All data is invented test data. Your job is to get it running and show that it works, not to change it.

## Run it
- Requirement: Python 3.9+ (`python3 --version`). Standard library only: do **not** `pip install` anything, create a venv, or add dependencies.
- From the repo root: `python3 app.py --reset` (resets to seed data, serves http://localhost:8000). Run it in the background or a separate terminal; it keeps running.
- Port busy (`Address already in use`): `PORT=8010 python3 app.py --reset`, then use that port everywhere below.
- No API key, internet or account needed. Model answers are canned (dry run). Do **not** set `live_runs` to true or ask the reviewer for a Lyceum key; live runs are deliberately off (too slow for a demo).

## Verify it
1. `./scripts/smoke_test.sh` (or `bash scripts/smoke_test.sh http://localhost:8010`). Must end with `ALL OK`. It uploads the sample Excel export, runs DD, changes a class, confirms a sunset and checks the permission rules. It changes data, so run `python3 app.py --reset` again before a manual walkthrough.
2. `python3 app.py --check-model`: validates the model step offline; expect `3/3 valid`.
3. Hand the reviewer http://localhost:8000 and the 7-step walkthrough under "For reviewers" in README.md. If you can drive a browser, follow those steps yourself.

## What "working" means (check these, they are the point of the demo)
- Target 1 IT Lead sees only their own tool list and questions UCM sent them; registry, docking plan, rule sheet and audit log return 403.
- Head of Field sees the plan without € figures or legal entries; confirm sunset / change class return 403.
- A K1 system is never set to switch off; every row has a reason and `decided_by`.
- Every action, including denied ones, appears in the audit log.

## Troubleshooting
- `python3: command not found` / version < 3.9: install Python 3 from python.org (macOS: `xcode-select --install` also provides it).
- Page shows old data or a broken state: stop the server (Ctrl+C) and start again with `--reset`.
- Login screen keeps reappearing: use the same host for everything (`localhost`, not `127.0.0.1` mixed with `localhost`); the session is a cookie.
- Windows: use `py app.py --reset`; run the smoke test from Git Bash or WSL, or skip it and use the browser walkthrough.

## Don'ts
- Don't edit code or data to make a check pass; report what failed, with the output.
- Don't commit or push; runtime files in `data/` are gitignored on purpose.
- Don't send repo data to external services.

Code map, if asked: `app.py` (HTTP server), `dal.py` (all permissions and persistence), `classifier.py` (rules + model step, read-only), `intake.py` / `xlsx.py` (Excel/CSV import), `static/index.html` (UI), `static/flow.js` (architecture panel), `config.json` (users, permissions, models).
