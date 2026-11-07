<img src="assets/icon.svg" width="64" height="64" alt="">

# DD Classifier + Registry (UCM case study prototype)

A target's tool list goes in, the docking plan comes out, before Day 0. **Test data only.**

```
python3 app.py --reset        # Python 3.9+, stdlib only → http://localhost:8000  (PORT=… to change)
```
Runs use canned model answers (dry run, offline). Live runs are switched off (`"live_runs": false` in config.json): a full live run took over 7 minutes because Kimi K3.1 Pro was at capacity and the Qwen fallback is slow.
`python3 app.py --check-model [--live]`: execute model on 3 undecided rows, validates the JSON (retry with the error, then fallback).

## For reviewers: open and test it (≈5 minutes)
Needs only Python 3.9+ (macOS/Linux have it; check with `python3 --version`). No install, no account, no internet, no API key.
```
git clone https://github.com/psystems-cmd/dd-registry-classifier.git    # or unzip the folder you received
cd dd-registry-classifier
python3 app.py --reset          # starts with fresh test data; leave this terminal open
```
Open http://localhost:8000 in a browser. If port 8000 is busy: `PORT=8010 python3 app.py --reset` and open http://localhost:8010. Stop with Ctrl+C; run `--reset` again any time to start over.

Log in with one of the four demo users (no passwords; "Switch user" top right) and walk through:
1. **Target 1 IT Lead** → *Our tool list*: upload the sample export (link on the page, or `data/samples/target_inventory_export.xlsx`). Check the column mapping. Note: only the tool list and *Questions from UCM* are visible, no plan, registry or log.
2. **Platform Lead** → *Docking plan* → **Run DD on target_1**. Check the summary (systems, K1/K2/K3, € saved, rules · model · human split, run cost) and the migration order.
3. Click **MS Teams** (⚠ next to the name): side panel → *ask ?* next to *Cost / year* → choose **Send to target_1** → Send.
4. In the same panel: change the class (pick K1, give a reason, Apply: it becomes read-only until Day 90, never switched off), set it back to K3, **Confirm sunset**, tick a knowledge item to extract.
5. **Target 1 IT Lead** → *Questions from UCM*: the question appears without any class, saving or sunset information. Answer it.
6. **Head of Field** → *Docking plan*: no € figures, no legal entries; open MS Teams and try *Apply* or *Confirm sunset*: the server answers 403.
7. **Platform Lead** → **Architecture** (top right) shows where each click went; *Audit log* lists every action, including the denied 403 attempts.

### Using an AI assistant to run it
- **Coding agent with a terminal** (Claude Code, Codex, Cursor…): open the repo folder and say *"Run and test this app following AGENTS.md."* The agent starts the server, runs `scripts/smoke_test.sh` (must print `ALL OK`) and hands you the URL. `CLAUDE.md` points Claude Code to the same file.
- **Plain chat** (claude.ai, ChatGPT; it can't run code on your machine): paste this and it will guide you step by step:
  > I have a Python demo app at https://github.com/psystems-cmd/dd-registry-classifier (read its README.md and AGENTS.md). It needs only Python 3.9+, no installs and no API key. Walk me through running it on my computer (I use macOS/Windows/Linux: pick mine), one command at a time, wait for my output after each step, and help me fix any error. Then guide me through the 7-step reviewer walkthrough in the README.

Optional: `python3 app.py --check-model` validates the model step offline; `curl -H "X-Session: …"` works against the same API (token from `POST /api/login`).

**Demo path (short):** Target 1 IT Lead uploads `/samples/target_inventory_export.xlsx` (Intake) and sees only their own list and questions from UCM → Platform Lead runs DD, clicks MS Teams (cost `incl. M365` ⚠) → *ask ?* on Cost / year → "Send to target_1" → Target answers → Platform Lead changes a class, confirms a sunset, ticks knowledge to extract → Head of Field sees the plan without € figures, a confirm returns 403 → Architecture panel shows where each click went → Audit log.

## How it works
- **Intake** (`intake.py`, `xlsx.py`): messy .xlsx/.csv export → header row detected, German/English columns mapped, totals rows skipped. The original upload is kept unchanged in `data/uploads/`.
- **Classifier** (`classifier.py`, read-only): PLAN model writes the rule sheet once; deterministic rules decide most rows; EXECUTE model only sees rows the rules can't decide. Guardrails on every row (K1 never switched off, finance stays local, deadlines per class). Money comes from the inventory, never from the model; unclear costs are marked, not guessed.
- **Data access layer** (`dal.py`): the only place that reads/writes data and enforces permissions: per-role company and data-class filters, field-level redaction (no € for Head of Field), 403 on writes. Every action and denied attempt → `data/audit.jsonl`.
- **Who sees what:** Target users see only their own tool list and the questions UCM sends them (questions to the target can only reference their own inventory fields), never the plan, the registry, savings, sunsets or the log. Head of Field: plan and registry (no legal entries, no €), no rule sheet, no log. Platform Lead / CEO: everything.
- **Models** (Lyceum, EU, `https://api.lyceum.technology/openai/v1`): plan `moonshotai/kimi-k3.1-pro` → fallback `qwen/qwen3.8-2.4t-a95b`; execute `z-ai/glm-5.3-flash` → fallback `deepseek/deepseek-v4-flash-0731`. Prices (USD / 1M tokens) in `config.json`. An unavailable model (e.g. 503 "capacity exhausted") switches to the fallback and is shown in the run.

## Missing for production
- Real inventory sources: licence exports, OAuth app list, SSO logs, expense data.
- Contracts and notice periods per tool (savings currently assume cancellation at switch-off).
- Real registry and real owners instead of roles.
- Real SSO and role mapping instead of preset demo users.
- Data processing agreement with Lyceum; inventory metadata (not personal data itself) is sent to the models.
