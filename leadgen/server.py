"""Inbound lead capture (landing page + form/JSON endpoint) and a token-protected dashboard.

  GET  /            landing page built from business.json -> "landing"
  POST /lead        form-encoded or JSON; honeypot field "website_url" must stay empty
  GET  /admin?token dashboard (requires LEADGEN_ADMIN_TOKEN; disabled if unset)
  POST /admin/status  update a lead's status
UTM params on the landing URL are stored in the lead's notes so you can see which ad/channel worked.
"""
import hmac
import html
import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, scoring
from .db import STATUSES, Store

e = html.escape
MAX_BODY = 20_000


def page(title, body):
    return (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{e(title)}</title><style>body{{font:16px/1.5 system-ui;max-width:42rem;margin:2rem auto;padding:0 1rem}}"
            f"input,textarea,select,button{{font:inherit;padding:.5rem;margin:.25rem 0;width:100%;box-sizing:border-box}}"
            f"table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{border-bottom:1px solid #ddd;padding:.3rem;text-align:left;vertical-align:top}}"
            f"button{{cursor:pointer}}.hot{{color:#b00020;font-weight:700}}.warm{{color:#b26a00}}</style>{body}")


def landing_html(cfg, utm):
    l = cfg["landing"]
    bullets = "".join(f"<li>{e(b)}</li>" for b in l.get("bullets", []))
    hidden = "".join(f"<input type=hidden name='{e(k)}' value='{e(v)}'>" for k, v in utm.items())
    return page(cfg["name"], f"""<h1>{e(l.get('headline', cfg['name']))}</h1><p>{e(l.get('subhead', cfg['offer']['summary']))}</p>
<ul>{bullets}</ul><form method=post action=/lead>{hidden}
<input name=name placeholder='Your name' required><input name=email type=email placeholder='Email' required>
<input name=phone placeholder='Phone (optional)'><input name=company placeholder='Company (optional)'>
<textarea name=message placeholder='What are you looking for?'></textarea>
<input name=website_url style='display:none' tabindex=-1 autocomplete=off>
<button>{e(l.get('button', 'Get in touch'))}</button>
<small>{e(l.get('consent', 'By submitting you agree to be contacted about this request.'))}</small></form>""")


def make_handler(slug):
    cfg = config.load(slug)
    store_path = config.db_path(slug)
    token = os.environ.get("LEADGEN_ADMIN_TOKEN", "")

    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="text/html; charset=utf-8", headers=()):
            b = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in headers:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(b)

        def _authed(self, qs, form=None):
            if not token:
                return False
            given = (qs.get("token") or (form or {}).get("token") or [""])[0]
            return hmac.compare_digest(given, token)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return None
            raw = self.rfile.read(n).decode("utf-8", "replace")
            if "json" in (self.headers.get("Content-Type") or ""):
                return {k: [str(v)] for k, v in json.loads(raw or "{}").items()}
            return urllib.parse.parse_qs(raw)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(u.query)
            if u.path == "/":
                utm = {k: v[0][:100] for k, v in qs.items() if k.startswith("utm_")}
                return self._send(200, landing_html(cfg, utm))
            if u.path == "/admin":
                if not self._authed(qs):
                    return self._send(403, "Forbidden (set LEADGEN_ADMIN_TOKEN and pass ?token=)")
                return self._send(200, self._dashboard())
            self._send(404, "Not found")

        def do_POST(self):
            u = urllib.parse.urlparse(self.path)
            try:
                form = self._body()
            except ValueError:
                return self._send(400, "Bad request")
            if form is None:
                return self._send(413, "Too large")
            g = lambda k: (form.get(k) or [""])[0].strip()
            if u.path == "/lead":
                if g("website_url"):  # honeypot tripped
                    return self._send(200, page("Thanks", "<h1>Thanks!</h1>"))
                if "@" not in g("email") or not g("name"):
                    return self._send(400, page("Error", "<p>Name and a valid email are required.</p>"))
                utm = ", ".join(f"{k}={g(k)}" for k in form if k.startswith("utm_"))
                store = Store(store_path)
                store.add("inbound", {"name": g("name")[:200], "email": g("email")[:200],
                                      "phone": g("phone")[:50], "company": g("company")[:200],
                                      "signal": g("message")[:1000], "notes": utm[:300]})
                scoring.score_all(store, cfg)
                store.conn.close()
                return self._send(200, page("Thanks", f"<h1>{e(cfg['landing'].get('thanks', 'Thanks - we will be in touch.'))}</h1>"))
            if u.path == "/admin/status":
                if not self._authed({}, form):
                    return self._send(403, "Forbidden")
                if g("status") in STATUSES and g("id").isdigit():
                    st = Store(store_path)
                    st.update(int(g("id")), status=g("status"))
                    st.conn.close()
                return self._send(303, "", headers=[("Location", f"/admin?token={urllib.parse.quote(token)}")])
            self._send(404, "Not found")

        def _dashboard(self):
            store = Store(store_path)
            st = store.stats()
            rows = ""
            for l in store.all(limit=300):
                opts = "".join(f"<option{' selected' if s == l['status'] else ''}>{s}</option>" for s in STATUSES)
                t = scoring.tier(l["score"], cfg)
                who = e(l["name"] or l["company"] or "")
                link = f"<a href='{e(l['url'])}' rel=noreferrer>{who}</a>" if (l["url"] or "").startswith("http") else who
                rows += (f"<tr><td class={t}>{l['score']}</td><td>{link}<br><small>{e(l['company'] or '')} {e(l['title'] or '')}</small></td>"
                         f"<td>{e(l['email'] or '')}<br>{e(l['phone'] or '')}</td><td>{e(l['source'])}</td>"
                         f"<td><small>{e((l['signal'] or '')[:120])}</small></td>"
                         f"<td><form method=post action=/admin/status><input type=hidden name=token value='{e(token)}'>"
                         f"<input type=hidden name=id value={l['id']}><select name=status onchange=this.form.submit()>{opts}</select></form></td></tr>")
            store.conn.close()
            return page(f"{cfg['name']} leads",
                        f"<h1>{e(cfg['name'])} - {st['total']} leads</h1><p>{e(json.dumps(st['by_source']))} {e(json.dumps(st['by_status']))}</p>"
                        f"<table><tr><th>Score<th>Lead<th>Contact<th>Source<th>Signal<th>Status</tr>{rows}</table>")

        def log_message(self, *a):
            pass

    return H


def serve(slug, host="127.0.0.1", port=8000):
    print(f"Serving {slug} on http://{host}:{port}  (admin: {'enabled' if os.environ.get('LEADGEN_ADMIN_TOKEN') else 'disabled - set LEADGEN_ADMIN_TOKEN'})")
    ThreadingHTTPServer((host, port), make_handler(slug)).serve_forever()
