"""Classifier. Read-only: it reads the inventory and the registry it is handed and returns results.
It never writes files and never touches source systems; persisting is the data layer's job (dal.py).

Model split: PLAN (strong model, once per run) writes the rule sheet; deterministic rules decide most
rows; EXECUTE (cheap model, per row) only sees rows the rules could not decide."""
import csv, datetime, json, os, re, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
CONFIG = json.loads((ROOT / "config.json").read_text())
INVENTORY = ROOT / "data" / "target_inventory.csv"
DRY = json.loads((ROOT / "data" / "dry_run.json").read_text())

CLASSES = {"K1", "K2", "K3", "needs_human"}
TRACKS = {"semantic_layer", "sweep", "sunset", "keep_local"}
ACTIONS = {"map_read_only", "migrate", "export_and_switch_off", "keep_local", "decide"}
DEADLINES = {"K1": "Day 90", "K2": "Day 60", "K3": "Day 30"}
RULES_TEXT = """K1 business-critical: if it fails, placements or payouts stop (CRM, student database, billing/payout).
  Action map_read_only: read-only connection until Day 90, short parallel run, then switch. Track semantic_layer.
K2 important: if it fails, daily work slows (SharePoint, shared drives, Excel lists). Action migrate into UCM by Day 60.
  Track semantic_layer for structured data (lists, CRM-like), sweep for documents.
K3 replaceable: duplicates a UCM tool (Teams vs Slack, own wiki vs Confluence) or unused.
  Action export_and_switch_off by Day 30. Track sunset.
Finance systems of the target stay local (founder's rule): track keep_local, action keep_local.
Anything the rules can't decide: class needs_human, action decide, with a reason. Never guess silently."""


# ---------- inventory (read-only) ----------
def norm(s):
    return re.sub(r"[^a-z0-9äöüß]+", " ", (s or "").lower()).strip()

def has(text, words):
    t = f" {norm(text)} "
    return next((w for w in words if norm(w) and f" {norm(w)} " in t), None)

def parse_cost(s):
    m = re.search(r"(?<![A-Za-z0-9])\d[\d.]*(,\d+)?", (s or "").replace(" ", ""))
    return float(m.group(0).replace(".", "").replace(",", ".")) if m else 0.0

def parse_date(s):
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.datetime.strptime((s or "").strip(), fmt).date()
        except ValueError:
            pass
    return None

def data_class(text):
    for cls in ("legal", "personal"):
        if has(text, CONFIG["data_class_keywords"][cls]):
            return cls
    return "internal"

def read_inventory(path=INVENTORY):
    with open(path, newline="", encoding="utf-8") as f:  # opened read-only, never written
        rows = []
        for i, raw in enumerate(csv.DictReader(f), 1):
            r = {k.strip().lower(): (v or "").strip() for k, v in raw.items()}
            d = parse_date(r["last_used"])
            r.update(row=i, cost=parse_cost(r["cost_per_year"]), last_used_date=d.isoformat() if d else None,
                     data_class=data_class(r["holds_data"] + " " + r["notes"]))
            rows.append(r)
        return rows


# ---------- model access (Lyceum, OpenAI-compatible) ----------
def has_key():
    return bool(os.environ.get(CONFIG["lyceum"]["api_key_env"]))

class LLM:
    def __init__(self, dry):
        self.dry, self.usage, self.calls, self.events, self._dry_seen = dry, {}, [], [], {}
        for m in CONFIG["models"].values():
            for name in (m["name"], m["fallback"]):
                if name in CONFIG["retiring_models"]:
                    raise ValueError(f"model {name} is marked Retiring; pick another in config.json")

    def call(self, kind, model, messages, key=None):
        if self.dry:  # canned responses; a list is replayed in order (to demo retry on invalid output)
            canned = DRY["plan"] if kind == "plan" else DRY["execute"].get(key, DRY["execute_default"])
            if isinstance(canned, list):
                i = self._dry_seen[key] = self._dry_seen.get(key, -1) + 1
                canned = canned[min(i, len(canned) - 1)]
            text = canned if isinstance(canned, str) else json.dumps(canned)
            usage = {"prompt_tokens": sum(len(m["content"]) for m in messages) // 4, "completion_tokens": len(text) // 4}
        else:
            base = os.environ.get("LYCEUM_BASE_URL", CONFIG["lyceum"]["base_url"])
            req = urllib.request.Request(
                base.rstrip("/") + "/chat/completions",
                data=json.dumps({"model": model, "messages": messages, "temperature": 0}).encode(),
                headers={"Content-Type": "application/json",
                         "Authorization": "Bearer " + os.environ[CONFIG["lyceum"]["api_key_env"]]})
            try:
                with urllib.request.urlopen(req, timeout=CONFIG["lyceum"]["timeout_s"]) as resp:
                    body = json.load(resp)
            except urllib.error.HTTPError as e:  # e.g. 503 "inference capacity exhausted"
                detail = parse_json(e.read().decode(errors="replace")) or {}
                raise ModelUnavailable(f"HTTP {e.code}: {(detail.get('error') or {}).get('message', e.reason)}")
            except OSError as e:  # URLError, socket.timeout
                raise ModelUnavailable(f"network: {e}")
            text, usage = body["choices"][0]["message"]["content"], body.get("usage", {})
        u = self.usage.setdefault(model, {"in": 0, "out": 0})
        u["in"] += usage.get("prompt_tokens", 0)
        u["out"] += usage.get("completion_tokens", 0)
        self.calls.append({"kind": kind, "model": model, "row": key})
        return text

    def cost(self):
        p = CONFIG["prices_per_mtok"]
        total = sum((u["in"] * p[m]["in"] + u["out"] * p[m]["out"]) / 1e6 for m, u in self.usage.items())
        return {"amount": round(total, 5), "currency": CONFIG["price_currency"], "tokens": self.usage,
                "calls": len(self.calls), "estimated": self.dry, "events": self.events}

class ModelUnavailable(Exception):
    pass

def parse_json(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None

def ask(llm, kind, messages, validate, key=None):
    """Primary model, retry once with the validation error, then the fallback model.
    An unavailable model (HTTP error, timeout) goes straight to the fallback; every switch is recorded."""
    cfg = CONFIG["models"][kind]
    for model in (cfg["name"], cfg["fallback"]):
        msgs = list(messages)
        for attempt in range(2):
            try:
                text = llm.call(kind, model, msgs, key)
            except ModelUnavailable as e:
                llm.events.append({"kind": kind, "row": key, "model": model, "event": f"unavailable ({e}), next model"})
                break
            data = parse_json(text)
            errs = validate(data) if data is not None else ["output is not valid JSON"]
            if not errs:
                return data, model
            llm.events.append({"kind": kind, "row": key, "model": model,
                               "event": f"invalid output, {'retry' if attempt == 0 else 'next model'}: {'; '.join(errs)}"})
            msgs += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "Invalid: " + "; ".join(errs) + ". Return the corrected JSON only."}]
    return None, None


# ---------- PLAN: strong model writes the rule sheet once ----------
SHEET_KEYS = ("classes", "keep_local_categories", "structured_categories", "alias_hints", "category_targets",
              "unused_days", "track_logic", "knowledge_templates")

def make_plan(samples, ucm, llm):
    ids = {e["id"] for e in ucm}
    reg = [{"id": e["id"], "name": e["name"], "aliases": e["aliases"]} for e in ucm]
    rows = [{k: r[k] for k in ("name", "vendor", "category", "holds_data", "notes")} for r in samples]
    prompt = (f"You write a compact rule sheet for classifying a target company's IT systems in due diligence.\n"
              f"Rules:\n{RULES_TEXT}\nUCM registry: {json.dumps(reg)}\nSample inventory rows: {json.dumps(rows)}\n"
              "Return ONLY a JSON object with keys: classes (K1, K2, K3: each {criteria, categories[]}), "
              "keep_local_categories[], structured_categories[], alias_hints {lowercase name fragment: UCM id}, "
              "category_targets {category keyword: UCM id}, unused_days (int), track_logic[] (short strings), "
              "knowledge_templates {category keyword or 'default': [{item, to: UCM id}]} = knowledge worth "
              "extracting from a system before it is switched off.")

    def validate(d):
        errs = [f"missing key {k}" for k in SHEET_KEYS if k not in d]
        errs += [f"unknown UCM id {v}" for k in ("alias_hints", "category_targets")
                 for v in (d.get(k) or {}).values() if v not in ids]
        return errs

    sheet, model = ask(llm, "plan", [{"role": "user", "content": prompt}], validate)
    if sheet is None:
        raise RuntimeError("plan model returned no valid rule sheet (after retry and fallback)")
    sheet["written_by"] = model
    return sheet


# ---------- rules first ----------
def decision(cls, track, ucm_match, action, reason):
    return {"class": cls, "track": track, "ucm_match": ucm_match, "action": action,
            "deadline": DEADLINES.get(cls, "Day 30"), "reason": reason}

def match_ucm(row, sheet, ucm):
    name = row["name"]
    for e in ucm:
        if any(has(name, [a]) for a in [e["name"], *e["aliases"]]):
            return e["id"], f"name matches UCM '{e['name']}'"
    for frag, eid in sheet["alias_hints"].items():
        if has(name, [frag]):
            return eid, f"rule sheet: '{frag}' ≈ {eid}"
    return None, None

def by_rules(row, sheet, ucm, today):
    cat, c = row["category"], sheet["classes"]
    target = next((eid for k, eid in sheet["category_targets"].items() if has(cat, [k])), None)
    if has(cat, sheet["keep_local_categories"]):
        return dict(decision("K2", "keep_local", None, "keep_local", "Finance system: stays local (founder's rule)."),
                    deadline="Day 90"), []
    if not row["owner_role"]:
        return decision("needs_human", "sweep", None, "decide",
                        "No owner listed: someone must own this before it is moved or switched off."), []
    if has(cat, c["K1"]["categories"]):
        return decision("K1", "semantic_layer", target, "map_read_only",
                        f"Business-critical ({cat}): read-only until Day 90, short parallel run, then switch."), []
    votes = []
    eid, why = match_ucm(row, sheet, ucm)
    if eid:
        votes.append(("K3", eid, f"Duplicates a UCM tool ({why})."))
    last = parse_date(row["last_used_date"] or "")
    if last and (today - last).days > sheet["unused_days"]:
        votes.append(("K3", None, f"Unused for {(today - last).days // 30} months."))
    if has(cat, c["K2"]["categories"]):
        votes.append(("K2", target, f"Important ({cat}): move into UCM systems by Day 60."))
    if len({v[0] for v in votes}) != 1:
        return None, votes  # nothing fired, or rules disagree: hand to the execute model
    cls = votes[0][0]
    match = next((v[1] for v in votes if v[1]), None)
    reason = " ".join(v[2] for v in votes)
    if cls == "K3":
        return decision("K3", "sunset", match, "export_and_switch_off", reason), votes
    track = "semantic_layer" if has(cat, sheet["structured_categories"]) else "sweep"
    return decision("K2", track, match, "migrate", reason), votes


# ---------- EXECUTE: cheap model, only for undecided rows ----------
def execute_row(row, sheet, votes, ids, llm):
    view = {k: row[k] for k in ("name", "vendor", "category", "users", "cost_per_year", "last_used",
                                "owner_role", "holds_data", "notes")}
    prompt = (f"Apply this rule sheet to ONE inventory row.\nRule sheet: {json.dumps(sheet)}\n"
              f"Rule hits (conflicting or none): {json.dumps(votes)}\nUCM registry ids: {json.dumps(sorted(ids))}\n"
              f"Row: {json.dumps(view)}\nReturn ONLY JSON: {{\"name\", \"class\": \"K1|K2|K3|needs_human\", "
              "\"track\": \"semantic_layer|sweep|sunset|keep_local\", \"ucm_match\": \"registry id or null\", "
              "\"action\": \"map_read_only|migrate|export_and_switch_off|keep_local|decide\", "
              "\"deadline\": \"Day 30|Day 60|Day 90\", \"saving_per_year\": number, \"reason\": \"one sentence\"}. "
              "If unsure, use class needs_human with action decide and say why.")

    def validate(d):
        errs = [f"{k} must be one of {sorted(allowed)}" for k, allowed in
                (("class", CLASSES), ("track", TRACKS), ("action", ACTIONS), ("deadline", {"Day 30", "Day 60", "Day 90"}))
                if d.get(k) not in allowed]
        if d.get("ucm_match") is not None and d.get("ucm_match") not in ids:
            errs.append("ucm_match must be a registry id or null")
        if not isinstance(d.get("reason"), str) or not d["reason"].strip():
            errs.append("reason must be one sentence")
        return errs

    d, model = ask(llm, "execute", [{"role": "user", "content": prompt}], validate, key=norm(row["name"]))
    if d is None:
        return decision("needs_human", "sweep", None, "decide", "Model output invalid after retry and fallback."), None
    return {k: d[k] for k in ("class", "track", "ucm_match", "action", "deadline", "reason")}, model


# ---------- guardrails, applied to every row regardless of who decided ----------
def guard(d):
    flags = []
    if d["class"] == "K1" and (d["action"] == "export_and_switch_off" or d["track"] == "sunset"):
        d.update(track="semantic_layer", action="map_read_only")
        flags.append("K1 is never switched off: proposal overridden to read-only until Day 90.")
    if d["class"] == "needs_human" and d["action"] != "decide":
        d["action"] = "decide"
        flags.append("needs_human rows get action 'decide'.")
    if d["class"] in DEADLINES and d["action"] != "keep_local" and d["deadline"] != DEADLINES[d["class"]]:
        flags.append(f"Deadline {d['deadline']} corrected to {DEADLINES[d['class']]} for {d['class']}.")
        d["deadline"] = DEADLINES[d["class"]]
    return flags

def knowledge_items(d, sheet):  # what to extract before a switch-off, from the plan model's templates
    t = sheet["knowledge_templates"]
    items = next((v for k, v in t.items() if k != "default" and has(d["category"], [k])), t.get("default", []))
    return [{"item": i["item"], "to": i["to"], "done": False} for i in items]

def saving(row, d):  # money comes from the inventory, not from the model
    if d["action"] == "export_and_switch_off" or (d["action"] in ("migrate", "map_read_only") and d["ucm_match"]):
        return row["cost"]
    return 0.0

ORDER = {"Day 30": 0, "Day 60": 1, "Day 90": 2}

def run(inventory, registry, dry, today=None):
    today = today or datetime.date.today()
    llm = LLM(dry)
    ucm = [e for e in registry if e["company"] == "ucm" and e["status"] != "archived"]
    ids, names = {e["id"] for e in ucm}, {e["id"]: e["name"] for e in ucm}
    sheet = make_plan(inventory[:5], ucm, llm)
    rows = []
    for r in inventory:
        d, votes = by_rules(r, sheet, ucm, today)
        decided_by, model = "rule", None
        if d is None:
            d, model = execute_row(r, sheet, votes, ids, llm)
            decided_by = "model" if model else "rule"
        d["guardrail"] = guard(d)
        d.update(name=r["name"], saving_per_year=saving(r, d), decided_by=decided_by, model=model,
                 owner_role=r["owner_role"], data_class=r["data_class"], category=r["category"],
                 cost_per_year=r["cost"], last_used=r["last_used_date"], holds_data=r["holds_data"],
                 ucm_name=names.get(d["ucm_match"]), cost_raw=r["cost_per_year"],
                 cost_unclear=bool(re.search(r"[^\d.,\s€]", r["cost_per_year"])))  # e.g. "incl. M365": ask the target
        d["knowledge"] = knowledge_items(d, sheet) if d["action"] == "export_and_switch_off" else []
        rows.append(d)
    rows.sort(key=lambda d: (d["class"] == "needs_human", ORDER[d["deadline"]], d["class"]))
    for i, d in enumerate(rows, 1):
        d["order"] = i
    return sheet, rows, llm.cost()
