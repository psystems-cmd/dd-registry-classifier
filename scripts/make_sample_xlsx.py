"""Build data/samples/target_inventory_export.xlsx, a deliberately messy finance-style export."""
import csv
import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import xlsx  # noqa: E402

KEEP_TEXT = {"MS Teams", "Asana"}
HEADERS = ["Tool / Anwendung", "Hersteller", "Kategorie", "Anzahl Nutzer", "Kosten p.a. (EUR)",
           "Zuletzt genutzt", "Verantwortlich", "Datenarten", "Bemerkung"]


def parse_date(s):
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return s


def num(s):
    return int(s) if s.isdigit() else s


rows = [["IT-Inventar target_1 – Export Finanzbuchhaltung (TEST DATA)"], [], HEADERS]
total = 0
with open(os.path.join(ROOT, "data", "target_inventory.csv"), newline="", encoding="utf-8") as f:
    for r in csv.DictReader(f):
        g = lambda k: r[k] or None
        cost = num(r["cost_per_year"]) if r["cost_per_year"] else None
        if isinstance(cost, int):
            total += cost
        used = r["last_used"]
        used = used if r["name"] in KEEP_TEXT or not used else parse_date(used)
        rows.append([g("name"), g("vendor"), g("category"),
                     num(r["users"]) if r["users"] else None, cost, used or None,
                     g("owner_role"), g("holds_data"), g("notes")])
rows += [[], ["Summe", None, None, None, total]]

out = os.path.join(ROOT, "data", "samples", "target_inventory_export.xlsx")
os.makedirs(os.path.dirname(out), exist_ok=True)
xlsx.write_rows(out, rows)
print("wrote", out)
