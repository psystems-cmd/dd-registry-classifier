<img src="assets/icon.svg" width="64" height="64" alt="">

# DD Classifier + Registry (UCM case study prototype)

A target's tool list goes in, the docking plan comes out, before Day 0. **Test data only.**

```
python3 app.py --reset        # Python 3.9+, stdlib only → http://localhost:8000  (PORT=… to change)
```
Runs use canned model answers (dry run, offline). Live runs are switched off (`"live_runs": false` in config.json): a full live run took over 7 minutes because Kimi K3.1 Pro was at capacity and the Qwen fallback is slow.
`python3 app.py --check-model [--live]`: execute model on 3 undecided rows, validates the JSON (retry with the error, then fallback).

**Demo path:** Target 1 IT Lead uploads `/samples/target_inventory_export.xlsx` (Intake) and sees only their own list and questions from UCM → Platform Lead runs DD, clicks MS Teams (cost `incl. M365` ⚠) → *ask ?* on Cost / year → "Send to target_1" → Target answers → Platform Lead changes a class, confirms a sunset, ticks knowledge to extract → Head of Field sees the plan without € figures, a confirm returns 403 → Architecture panel shows where each click went → Audit log.

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
