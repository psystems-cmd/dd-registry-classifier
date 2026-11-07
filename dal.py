"""Data access layer: auth, permissions, persistence, audit.
Every read is filtered by the user's companies and data classes before it is returned, and every write checks
a permission here. The UI and the classifier never decide who may see or do what."""
import datetime, hashlib, hmac, json, re, secrets, shutil
from pathlib import Path
import classifier, intake

ROOT = Path(__file__).parent
DATA = ROOT / "data"
CONFIG = classifier.CONFIG
USERS = CONFIG["users"]
STATUSES = {"active", "migrating", "sunset_planned", "archived"}
INV_SEED, INV_ACTIVE, UPLOADS = DATA / "target_inventory.csv", DATA / "inventory_active.csv", DATA / "uploads"
ACTION_FOR = {"K1": "map_read_only", "K2": "migrate", "K3": "export_and_switch_off", "needs_human": "decide"}
STATUS_FOR = {"export_and_switch_off": "sunset_planned", "migrate": "migrating", "map_read_only": "migrating"}


class Forbidden(Exception):
    pass


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

def _load(name, default=None):
    p = DATA / name
    return json.loads(p.read_text()) if p.exists() else default

def _save(name, obj):
    tmp = DATA / (name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False))
    tmp.replace(DATA / name)

def init(reset=False):
    if reset or not (DATA / "registry.json").exists():
        shutil.copy(DATA / "seed" / "registry.json", DATA / "registry.json")
    if reset:
        for n in ("results.json", "rules.json", "audit.jsonl", "questions.json", "intake.json", "inventory_active.csv"):
            (DATA / n).unlink(missing_ok=True)
        shutil.rmtree(UPLOADS, ignore_errors=True)


# ---------- dummy auth: preset users, HMAC-signed session token (cookie or X-Session header) ----------
def _secret():
    p = DATA / ".secret"
    if not p.exists():
        p.write_text(secrets.token_hex(32))
    return p.read_text().encode()

def _sign(uid):
    return uid + "." + hmac.new(_secret(), uid.encode(), hashlib.sha256).hexdigest()[:32]

def list_users():
    return [{"id": k, **{f: u[f] for f in ("name", "role", "company", "blurb")}} for k, u in USERS.items()]

def login(uid):
    if uid not in USERS:
        raise ValueError("unknown user")
    user = {"id": uid, **USERS[uid]}
    audit(user, "login", uid)
    return _sign(uid)

def user_from_token(tok):
    uid = (tok or "").rpartition(".")[0]
    if uid in USERS and hmac.compare_digest(_sign(uid), tok):
        return {"id": uid, **USERS[uid]}
    return None

def me(user):
    return {k: user[k] for k in ("id", "name", "role", "company", "companies", "hidden_classes", "hidden_fields", "perms")}


# ---------- permission primitives ----------
def _can_see(user, company, data_class):
    return ("*" in user["companies"] or company in user["companies"]) and data_class not in user["hidden_classes"]

def _require(user, perm, target):
    if perm not in user["perms"]:
        audit(user, "denied:" + perm, target)
        if user["company"] != "ucm":  # don't tell target users which actions exist
            raise Forbidden("not permitted for your role")
        raise Forbidden(f"{user['role']} ({user['company']}) may not {perm}")

def _redact(user, obj):  # field-level need-to-know, e.g. no € figures for Head of Field
    hidden = set(user["hidden_fields"])
    if isinstance(obj, list):
        return [_redact(user, o) for o in obj]
    if isinstance(obj, dict):
        return {k: _redact(user, v) for k, v in obj.items() if k not in hidden}
    return obj

def audit(user, action, target, **detail):
    entry = {"at": now(), "who": user["id"], "role": user["role"], "company": user["company"],
             "action": action, "target": target, **detail}
    with open(DATA / "audit.jsonl", "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------- one function per resource ----------
def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")

def _registry(user):
    return [e for e in _load("registry.json", []) if _can_see(user, e["company"], e["data_class"])]

def get_registry(user):
    _require(user, "view_registry", "registry")  # target users never see UCM's registry, incl. its plan for them
    return _redact(user, _registry(user))

def _inventory(user):
    rows = classifier.read_inventory(INV_ACTIVE if INV_ACTIVE.exists() else INV_SEED)
    return [{**r, "id": "inv-" + _slug(r["name"])} for r in rows if _can_see(user, "target_1", r["data_class"])]

def get_inventory(user):
    _require(user, "view_inventory", "inventory")
    return _redact(user, _inventory(user))

def get_intake(user):
    _require(user, "view_inventory", "intake")
    return _load("intake.json") or {"source": "seed", "file": INV_SEED.name, "by": "seed data", "at": None,
                                    "mapping": [{"column": f, "field": f} for f in intake.FIELDS], "warnings": []}

def get_rules(user):
    _require(user, "view_rules", "rules")  # holds UCM registry ids, so not for target users
    return _load("rules.json")

def get_results(user):
    _require(user, "view_results", "results")
    res = _load("results.json")
    if not res:
        return None
    visible = {e["id"] for e in _registry(user)}
    rows = []
    for r in res["rows"]:
        if _can_see(user, r["company"], r["data_class"]):
            hidden = r["ucm_match"] and r["ucm_match"] not in visible
            rows.append({**r, **({"ucm_match": "(hidden for your role)", "ucm_name": None} if hidden else {})})
    stale = res.get("inventory_at") != (_load("intake.json") or {}).get("at")
    return _redact(user, {**res, "rows": rows, "summary": summarize(rows, res), "stale": stale})

def get_audit(user):
    _require(user, "view_audit", "audit")
    p = DATA / "audit.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()][::-1] if p.exists() else []

TARGET_VIEW = ("id", "kind", "entity_label", "inv_id", "field", "text", "at", "status", "answers")

def get_questions(user):
    qs = [q for q in _load("questions.json", []) if _can_see(user, q["company"], q["data_class"])][::-1]
    if user["company"] != "ucm":  # targets only see what UCM deliberately sent them, stripped of plan context
        return [{**{k: q[k] for k in TARGET_VIEW}, "asked_by": "UCM integration team"}
                for q in qs if q["audience"] == "target"]
    return _redact(user, qs)

def summarize(rows, res):
    count = lambda pred: sum(1 for r in rows if pred(r))
    human = lambda r: r["class"] == "needs_human"
    return {"total": len(rows),
            "by_class": {c: count(lambda r, c=c: r["class"] == c) for c in ("K1", "K2", "K3", "needs_human")},
            "saving_per_year": sum(r["saving_per_year"] for r in rows),
            "by_rules": count(lambda r: r["decided_by"] == "rule" and not human(r)),
            "by_model": count(lambda r: r["decided_by"] == "model" and not human(r)),
            "by_human": count(human),
            "run_cost": res["cost"]}


# ---------- writes ----------
def upload_inventory(user, filename, data):
    _require(user, "upload_inventory", filename)
    if len(data) > 2_000_000:
        raise ValueError("file too large (max 2 MB)")
    rows, meta = intake.map_table(intake.table_from_bytes(filename, data))
    if not rows:
        raise ValueError("no tool rows found under the header")
    safe = re.sub(r"[^\w.-]", "_", Path(filename).name)[-80:]
    UPLOADS.mkdir(exist_ok=True)
    (UPLOADS / f"{now().replace(':', '')}-{safe}").write_bytes(data)  # original kept unchanged
    INV_ACTIVE.write_text(intake.to_csv(rows))
    meta.update(source="upload", file=safe, by=user["role"], at=now(), rows=len(rows))
    _save("intake.json", meta)
    audit(user, "upload_inventory", safe, rows=len(rows), warnings=len(meta["warnings"]))
    return meta

def _registry_entry(r, prev):
    kind = "data_source" if _slug(r["name"]).endswith("xlsx") else "tool"
    eid = f"target_1-{_slug(r['owner_role']) or 'unowned'}-{kind}-{_slug(r['name'])[:30]}"
    entry = {"id": eid, "name": r["name"], "type": kind, "company": "target_1",
             "owner": r["owner_role"] or "unassigned", "purpose": f"Target system ({r['category']}): {r['holds_data']}.",
             "data_kind": "structured" if r["track"] == "semantic_layer" else "unstructured",
             "data_class": r["data_class"], "criticality": r["class"], "status": STATUS_FOR.get(r["action"], "active"),
             "cost_per_year": r["cost_per_year"], "last_used": r["last_used"], "replaces": None,
             "replaced_by": r["ucm_match"] if r["action"] in STATUS_FOR else None,
             "aliases": [r["name"]], "test_data": True}
    old = prev.get(eid, {})
    if entry["status"] == "sunset_planned" and old.get("sunset_confirmed_by"):
        entry.update(sunset_confirmed_by=old["sunset_confirmed_by"], sunset_confirmed_at=old["sunset_confirmed_at"])
    return entry

def run_dd(user, dry_run=True):
    _require(user, "run_dd", "dd:target_1")
    dry = bool(dry_run) or not classifier.has_key() or not CONFIG["live_runs"]  # live runs switched off for now
    registry = _load("registry.json", [])
    inventory = classifier.read_inventory(INV_ACTIVE if INV_ACTIVE.exists() else INV_SEED)
    sheet, rows, cost = classifier.run(inventory, registry, dry)
    prev = {e["id"]: e for e in registry if e["company"] == "target_1"}
    new_entries = [_registry_entry(r, prev) for r in rows]
    for r, e in zip(rows, new_entries):
        r.update(id=e["id"], company="target_1", sunset_confirmed_by=e.get("sunset_confirmed_by"))
    _save("registry.json", [e for e in registry if e["company"] != "target_1"] + new_entries)
    run_id = "run-" + now().replace(":", "")
    _save("rules.json", {"run_id": run_id, **sheet})
    _save("results.json", {"run_id": run_id, "at": now(), "by": user["role"], "dry_run": dry,
                           "inventory_at": (_load("intake.json") or {}).get("at"),
                           "models": CONFIG["models"], "cost": cost, "rows": rows})
    split = summarize(rows, {"cost": cost})
    audit(user, "run_dd", "target_1", run_id=run_id, dry_run=dry, cost=cost["amount"], currency=cost["currency"],
          split=f"{split['by_rules']} rules / {split['by_model']} model / {split['by_human']} human")
    return get_results(user)

def _find_row(user, res, row_id):
    row = next((r for r in (res or {}).get("rows", []) if r["id"] == row_id), None)
    if not row or not _can_see(user, row["company"], row["data_class"]):
        raise KeyError(f"no result {row_id}")
    return row

def _save_row(res, row, **registry_fields):
    registry = _load("registry.json", [])
    for e in registry:
        if e["id"] == row["id"]:
            e.update(registry_fields)
    _save("results.json", res)
    _save("registry.json", registry)

def confirm_sunset(user, row_id):
    _require(user, "confirm_sunset", row_id)
    res = _load("results.json")
    row = _find_row(user, res, row_id)
    if row["class"] == "K1" or row["action"] != "export_and_switch_off":
        raise ValueError("only K2/K3 rows planned for switch-off can be confirmed")
    row.update(sunset_confirmed_by=user["role"], sunset_confirmed_at=now())
    _save_row(res, row, sunset_confirmed_by=user["role"], sunset_confirmed_at=row["sunset_confirmed_at"])
    open_items = sum(1 for k in row.get("knowledge", []) if not k["done"])
    audit(user, "confirm_sunset", row_id, name=row["name"], knowledge_open=open_items)
    return row

def flag_human(user, row_id, reason):
    _require(user, "flag_human", row_id)
    if not reason.strip():
        raise ValueError("a reason is required")
    res = _load("results.json")
    row = _find_row(user, res, row_id)
    row.update(previous={k: row[k] for k in ("class", "action", "reason", "decided_by")}, decided_by="human",
               reason=f"Flagged by {user['role']}: {reason.strip()}", flagged_by=user["role"], sunset_confirmed_by=None,
               **{"class": "needs_human", "action": "decide"})
    _save_row(res, row, criticality="needs_human", status="active", sunset_confirmed_by=None)  # no longer a sunset
    audit(user, "flag_needs_human", row_id, reason=reason.strip())
    return row

def override_class(user, row_id, cls, reason):
    _require(user, "override_class", row_id)
    if cls not in classifier.CLASSES:
        raise ValueError(f"class must be one of {sorted(classifier.CLASSES)}")
    if not str(reason).strip():
        raise ValueError("a reason is required to change a class")
    res = _load("results.json")
    row = _find_row(user, res, row_id)
    prev = {k: row[k] for k in ("class", "track", "action", "deadline", "reason", "decided_by")}
    action, flags = ACTION_FOR[cls], []
    keep = row["track"] if row["track"] in ("semantic_layer", "sweep") else "sweep"
    track = {"K1": "semantic_layer", "K3": "sunset"}.get(cls, keep)
    if row["track"] == "keep_local" and cls != "needs_human":  # founder's rule beats a manual class change
        track, action = "keep_local", "keep_local"
        flags.append("Finance system stays local (founder's rule): class changed, action kept.")
    row.update(previous=prev, track=track, action=action, decided_by="human", deadline=classifier.DEADLINES.get(cls, "Day 30"),
               reason=f"Changed {prev['class']} → {cls} by {user['role']}: {str(reason).strip()}", **{"class": cls})
    if action == "keep_local":
        row["deadline"] = "Day 90"
    row["guardrail"] = flags + classifier.guard(row)
    row["saving_per_year"] = classifier.saving({"cost": row["cost_per_year"]}, row)
    if action == "export_and_switch_off":
        row["knowledge"] = row.get("knowledge") or classifier.knowledge_items(row, _load("rules.json"))
    else:
        row["sunset_confirmed_by"] = None
    _save_row(res, row, criticality=cls, status=STATUS_FOR.get(action, "active"),
              sunset_confirmed_by=row.get("sunset_confirmed_by"))
    audit(user, "override_class", row_id, before=prev["class"], after=cls, reason=str(reason).strip())
    return row

def extract_knowledge(user, row_id, idx, done):
    _require(user, "extract_knowledge", row_id)
    res = _load("results.json")
    row = _find_row(user, res, row_id)
    items = row.get("knowledge") or []
    if not 0 <= idx < len(items):
        raise KeyError(f"no knowledge item {idx}")
    items[idx].update(done=bool(done), done_by=user["role"] if done else None, done_at=now() if done else None)
    _save("results.json", res)
    audit(user, "extract_knowledge", row_id, item=items[idx]["item"], done=bool(done))
    return row

def edit_registry(user, entry_id, changes):
    _require(user, "edit_registry", entry_id)
    registry = _load("registry.json", [])
    entry = next((e for e in registry if e["id"] == entry_id and _can_see(user, e["company"], e["data_class"])), None)
    if not entry:
        raise KeyError(f"no registry entry {entry_id}")
    clean = {k: v for k, v in changes.items() if k in ("status", "owner")}
    if "status" in clean and clean["status"] not in STATUSES:
        raise ValueError(f"status must be one of {sorted(STATUSES)}")
    entry.update(clean)
    _save("registry.json", registry)
    audit(user, "edit_registry", entry_id, changes=clean)
    return entry


# ---------- questions and data issues ----------
# audience "internal": UCM only. audience "target": sent to target_1, may only reference their own inventory fields,
# so the question itself cannot leak UCM's plan (classes, savings, sunsets).
def _entity(user, etype, eid):
    if etype == "result":
        r = _find_row(user, _load("results.json"), eid)
        return r["company"], r["data_class"], r["name"]
    pool = {"registry": _registry, "inventory": _inventory}.get(etype)
    e = next((e for e in pool(user) if e["id"] == eid), None) if pool else None
    if not e:
        raise KeyError(f"no {etype} {eid}")
    return e.get("company", "target_1"), e["data_class"], e["name"]

def ask(user, b):
    audience = b.get("audience", "internal")
    _require(user, "ask_target" if audience == "target" else "ask", "question")
    text, kind, field = str(b.get("text", "")).strip(), b.get("kind", "question"), b.get("field") or None
    if not text or kind not in ("question", "issue") or audience not in ("internal", "target"):
        raise ValueError("text required; kind is question or issue; audience is internal or target")
    company, dc, label = _entity(user, b.get("entity_type"), b.get("entity_id"))
    if audience == "target" and (company != "target_1" or (field and field not in intake.FIELDS)):
        raise ValueError(f"questions to the target may only reference their own inventory fields: {intake.FIELDS}")
    qs = _load("questions.json", [])
    q = {"id": f"q-{len(qs) + 1}", "kind": kind, "audience": audience, "entity_type": b["entity_type"],
         "entity_id": b["entity_id"], "entity_label": label, "inv_id": "inv-" + _slug(label), "field": field,
         "text": text, "asked_by": user["role"], "asked_by_id": user["id"], "at": now(), "status": "open",
         "answers": [], "company": company, "data_class": dc}
    _save("questions.json", qs + [q])
    audit(user, f"ask:{kind}:{audience}", b["entity_id"], question=q["id"], field=field, text=text)
    return q

def _find_q(user, qs, qid):
    q = next((q for q in qs if q["id"] == qid), None)
    if not q or not _can_see(user, q["company"], q["data_class"]) or (user["company"] != "ucm" and q["audience"] != "target"):
        raise KeyError(f"no question {qid}")
    return q

def answer(user, qid, text):
    _require(user, "answer_target" if user["company"] != "ucm" else "ask", qid)
    if not str(text).strip():
        raise ValueError("answer text required")
    qs = _load("questions.json", [])
    q = _find_q(user, qs, qid)
    by = user["role"] if user["company"] == "ucm" else f"{user['role']} (target_1)"
    q["answers"].append({"by": by, "at": now(), "text": str(text).strip()})
    q["status"] = "answered"
    _save("questions.json", qs)
    audit(user, "answer", qid, text=str(text).strip())
    return q if user["company"] == "ucm" else {k: q[k] for k in TARGET_VIEW}

def resolve(user, qid):
    qs = _load("questions.json", [])
    q = _find_q(user, qs, qid)
    if q["asked_by_id"] != user["id"]:
        _require(user, "resolve_questions", qid)  # the asker, or Platform Lead / CEO
    q.update(status="resolved", resolved_by=user["role"])
    _save("questions.json", qs)
    audit(user, "resolve", qid)
    return q
