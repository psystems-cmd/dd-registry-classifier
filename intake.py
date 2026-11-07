"""Intake: turn a messy .xlsx/.csv export into the inventory schema (the 'migration' step before DD).
Pure: takes bytes, returns rows plus a mapping report. Persisting is dal.py's job."""
import csv, datetime, io, re
import xlsx

FIELDS = ["name", "vendor", "category", "users", "cost_per_year", "last_used", "owner_role", "holds_data", "notes"]
SYNONYMS = {
    "name": ["name", "tool", "tool anwendung", "anwendung", "system", "application", "software", "tool name"],
    "vendor": ["vendor", "hersteller", "anbieter", "supplier", "lieferant"],
    "category": ["category", "kategorie", "typ", "type", "art"],
    "users": ["users", "nutzer", "anzahl nutzer", "user", "lizenzen", "seats"],
    "cost_per_year": ["cost per year", "kosten", "kosten p a", "kosten p a eur", "jahreskosten", "cost", "annual cost"],
    "last_used": ["last used", "zuletzt genutzt", "letzte nutzung", "last login", "last activity"],
    "owner_role": ["owner role", "owner", "verantwortlich", "ansprechpartner", "zustaendig", "zuständig"],
    "holds_data": ["holds data", "daten", "datenarten", "data", "data types"],
    "notes": ["notes", "bemerkung", "kommentar", "notizen", "comment", "comments"],
}
SKIP_NAMES = re.compile(r"^(summe|total|gesamt)\b", re.I)


def norm(s):
    return re.sub(r"[^a-z0-9äöüß]+", " ", str(s or "").lower()).strip()

def table_from_bytes(filename, data):
    name = filename.lower()
    if name.endswith((".xlsx", ".xlsm")):
        return xlsx.read_rows(data)
    if name.endswith((".csv", ".txt")):
        text = data.decode("utf-8-sig", errors="replace")
        delim = ";" if text.count(";") > text.count(",") else ","
        return list(csv.reader(io.StringIO(text), delimiter=delim))
    raise ValueError("upload an .xlsx or .csv file")

def field_for(header):
    h = norm(header)
    for exact in (True, False):
        for field, syns in SYNONYMS.items():
            if any(h == norm(s) if exact else h.startswith(norm(s) + " ") for s in syns):
                return field
    return None

def map_table(table):
    for i, header in enumerate(table[:15]):
        hits = [field_for(c) for c in header]
        if "name" in hits and sum(1 for h in hits if h) >= 3:
            break
    else:
        raise ValueError("no header row found: need a name/tool column and at least 3 known columns")
    mapping, seen = [], set()
    for col, field in zip(header, hits):
        field = None if field in seen else field
        seen.add(field)
        mapping.append({"column": col, "field": field})
    rows, warnings = [], []
    for line, raw in enumerate(table[i + 1:], i + 2):
        if not any(str(v).strip() for v in raw):
            continue
        r = dict.fromkeys(FIELDS, "")
        for j, m in enumerate(mapping):
            if m["field"] and j < len(raw):
                r[m["field"]] = str(raw[j]).strip()
        if not r["name"] or SKIP_NAMES.match(r["name"]):
            warnings.append(f"line {line}: '{r['name'] or '(empty name)'}' skipped (no tool name / totals row)")
            continue
        if re.fullmatch(r"\d{5}(\.0+)?", r["last_used"]):  # raw Excel date serial
            r["last_used"] = (datetime.date(1899, 12, 30) + datetime.timedelta(days=int(float(r["last_used"])))).isoformat()
        rows.append(r)
    missing = [f for f in FIELDS if f not in seen]
    warnings += [f"column '{f}' not found: left empty" for f in missing]
    ignored = [m["column"] for m in mapping if not m["field"] and str(m["column"]).strip()]
    warnings += [f"column '{c}' not recognised: ignored" for c in ignored]
    return rows, {"header_row": i + 1, "mapping": mapping, "missing": missing, "warnings": warnings}

def to_csv(rows):
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=FIELDS)
    w.writeheader()
    w.writerows(rows)
    return out.getvalue()
