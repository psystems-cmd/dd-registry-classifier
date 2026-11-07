#!/usr/bin/env python3
"""DD Classifier + Registry demo server (stdlib only).
  python3 app.py                 serve on http://localhost:8000
  python3 app.py --reset         restore seed registry, clear results and audit log, then serve
  python3 app.py --check-model   run the execute model on 3 undecided rows and validate the JSON (add --live for Lyceum)"""
import datetime, json, os, re, sys, urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import classifier, dal

GET = {"/api/me": dal.me, "/api/registry": dal.get_registry, "/api/inventory": dal.get_inventory,
       "/api/intake": dal.get_intake, "/api/rules": dal.get_rules, "/api/results": dal.get_results,
       "/api/questions": dal.get_questions, "/api/audit": dal.get_audit}
POST = [(r"/api/run", lambda u, b, m: dal.run_dd(u, b.get("dry_run", True))),
        (r"/api/results/([\w.-]+)/confirm", lambda u, b, m: dal.confirm_sunset(u, m[1])),
        (r"/api/results/([\w.-]+)/flag", lambda u, b, m: dal.flag_human(u, m[1], str(b.get("reason", "")))),
        (r"/api/results/([\w.-]+)/class", lambda u, b, m: dal.override_class(u, m[1], b.get("class"), b.get("reason", ""))),
        (r"/api/results/([\w.-]+)/knowledge/(\d+)", lambda u, b, m: dal.extract_knowledge(u, m[1], int(m[2]), b.get("done", True))),
        (r"/api/questions", lambda u, b, m: dal.ask(u, b)),
        (r"/api/questions/([\w.-]+)/answer", lambda u, b, m: dal.answer(u, m[1], b.get("text", ""))),
        (r"/api/questions/([\w.-]+)/resolve", lambda u, b, m: dal.resolve(u, m[1])),
        (r"/api/registry/([\w.-]+)", lambda u, b, m: dal.edit_registry(u, m[1], b))]
STATIC = {"/static/flow.js": ("static/flow.js", "text/javascript; charset=utf-8"),
          "/samples/target_inventory_export.xlsx": ("data/samples/target_inventory_export.xlsx",
          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, ctype="application/json", cookie=None):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if cookie is not None:
            self.send_header("Set-Cookie", f"session={cookie}; HttpOnly; SameSite=Strict; Path=/")
        self.end_headers()
        self.wfile.write(data)

    def user(self):
        tok = self.headers.get("X-Session")
        if not tok:
            c = SimpleCookie(self.headers.get("Cookie", ""))
            tok = c["session"].value if "session" in c else None
        return dal.user_from_token(tok)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n)) if n else {}

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def route(self, method):
        path = self.path.split("?")[0]
        try:
            if path == "/":
                return self.send(200, (dal.ROOT / "static" / "index.html").read_bytes(), "text/html; charset=utf-8")
            if path in STATIC:
                return self.send(200, (dal.ROOT / STATIC[path][0]).read_bytes(), STATIC[path][1])
            if path == "/api/users":
                return self.send(200, dal.list_users())
            if path == "/api/login" and method == "POST":
                tok = dal.login(self.body().get("user_id"))
                return self.send(200, {"ok": True, "token": tok}, cookie=tok)
            if path == "/api/logout":
                return self.send(200, {"ok": True}, cookie="")
            user = self.user()
            if not user:
                return self.send(401, {"error": "not logged in"})
            if method == "POST" and path == "/api/inventory/upload":  # raw file body, name in X-Filename
                n = min(int(self.headers.get("Content-Length") or 0), 2_000_001)
                return self.send(200, dal.upload_inventory(user, urllib.parse.unquote(self.headers.get("X-Filename", "")), self.rfile.read(n)))
            if method == "GET" and path in GET:
                return self.send(200, GET[path](user))
            for pattern, fn in POST if method == "POST" else []:
                m = re.fullmatch(pattern, path)
                if m:
                    return self.send(200, fn(user, self.body(), m))
            self.send(404, {"error": "not found"})
        except dal.Forbidden as e:
            self.send(403, {"error": str(e)})
        except (ValueError, KeyError) as e:
            self.send(400, {"error": str(e).strip("'\"")})
        except Exception as e:  # keep the demo alive and show the problem
            self.send(500, {"error": f"{type(e).__name__}: {e}"})

    def log_message(self, fmt, *args):
        pass


def check_model(live):
    """Brief: before wiring the UI, the execute model must return valid JSON for 3 rows."""
    if live and not classifier.has_key():
        sys.exit(f"--live needs {classifier.CONFIG['lyceum']['api_key_env']} set")
    llm = classifier.LLM(dry=not live)
    registry = json.loads((dal.DATA / "seed" / "registry.json").read_text())
    ucm = [e for e in registry if e["company"] == "ucm" and e["status"] != "archived"]
    inv = classifier.read_inventory()
    sheet = classifier.make_plan(inv[:5], ucm, llm)
    undecided = [(r, v) for r in inv for d, v in [classifier.by_rules(r, sheet, ucm, datetime.date.today())] if d is None]
    ok = 0
    for row, votes in undecided[:3]:
        d, model = classifier.execute_row(row, sheet, votes, {e["id"] for e in ucm}, llm)
        ok += model is not None
        print(f"{'OK ' if model else 'BAD'} {row['name']:<34} via {model}: {json.dumps(d)}")
    c = llm.cost()
    for e in c["events"]:
        print(f"  {e['kind']} {e['model']}: {e['event']}")
    print(f"{ok}/3 valid · {c['calls']} calls (incl. retries) · cost {c['currency']} {c['amount']} · "
          f"{'live' if live else 'dry run'}")


if __name__ == "__main__":
    if "--check-model" in sys.argv:
        check_model("--live" in sys.argv)
        sys.exit()
    dal.init(reset="--reset" in sys.argv)
    port = int(os.environ.get("PORT", classifier.CONFIG["port"]))
    mode = "live models available" if classifier.has_key() else "no LYCEUM_API_KEY: dry run only"
    print(f"DD Classifier + Registry on http://localhost:{port}  ({mode})")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
