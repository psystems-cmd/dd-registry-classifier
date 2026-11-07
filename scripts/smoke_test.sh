#!/usr/bin/env bash
# Smoke test against a running server: ./scripts/smoke_test.sh [base_url]
# Walks the demo path over the API and checks the permission rules. Uses test data only.
set -euo pipefail
U="${1:-http://localhost:8000}"
fail() { echo "FAIL: $*"; exit 1; }
tok() { curl -sf -X POST "$U/api/login" -d "{\"user_id\":\"$1\"}" | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])'; }
code() { curl -s -o /dev/null -w '%{http_code}' -H "X-Session: $1" "${@:3}" "$U$2"; }
post() { local body="${3:-}"; [ -n "$body" ] || body='{}'; curl -sf -X POST -H "X-Session: $1" -d "$body" "$U$2"; }

curl -sf -o /dev/null "$U/" || fail "server not reachable at $U (start it with: python3 app.py --reset)"
T=$(tok target1_it); P=$(tok platform_lead); H=$(tok head_of_field)

rows=$(curl -sf -X POST -H "X-Session: $T" -H "X-Filename: target_inventory_export.xlsx" \
  --data-binary @"$(dirname "$0")/../data/samples/target_inventory_export.xlsx" "$U/api/inventory/upload" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["rows"])')
[ "$rows" = 17 ] || fail "upload mapped $rows rows, expected 17"; echo "ok  upload: 17 rows mapped"

post "$P" /api/run | python3 -c 'import sys,json;s=json.load(sys.stdin)["summary"];assert s["total"]==17,s;print("ok  run: K1/K2/K3/human =",s["by_class"])'

ID=target_1-it-lead-tool-ms-teams
post "$P" "/api/results/$ID/class" '{"class":"K1","reason":"smoke test"}' | python3 -c 'import sys,json;d=json.load(sys.stdin);assert d["action"]=="map_read_only",d;print("ok  K1 is read-only, never switched off")'
post "$P" "/api/results/$ID/class" '{"class":"K3","reason":"smoke test"}' >/dev/null
post "$P" "/api/results/$ID/confirm" >/dev/null && echo "ok  Platform Lead confirmed sunset"

[ "$(code "$H" "/api/results/$ID/confirm" -X POST)" = 403 ] || fail "Head of Field confirm should be 403"; echo "ok  Head of Field confirm -> 403"
for e in registry results rules audit; do [ "$(code "$T" /api/$e)" = 403 ] || fail "target sees /api/$e"; done; echo "ok  target user: registry/results/rules/audit -> 403"
curl -sf -H "X-Session: $H" "$U/api/results" | python3 -c 'import sys;s=sys.stdin.read();assert "saving_per_year" not in s;print("ok  Head of Field sees no € figures")'

post "$P" /api/questions "{\"entity_type\":\"result\",\"entity_id\":\"$ID\",\"field\":\"cost_per_year\",\"audience\":\"target\",\"text\":\"What does Teams cost per year?\"}" >/dev/null
curl -sf -H "X-Session: $T" "$U/api/questions" | python3 -c 'import sys,json;q=json.load(sys.stdin);assert len(q)==1 and "audience" not in q[0];print("ok  question reached target without plan context")'
echo "ALL OK"
