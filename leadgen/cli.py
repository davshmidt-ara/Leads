import argparse
import json
import os
import shutil
import sys
import urllib.error
import urllib.request

from . import config, csvio, enrich, outreach, scoring
from .db import STATUSES, Store, display_name
from .sources import REGISTRY

TEMPLATE = os.path.join(config.ROOT, "example", "business.json")
PROBE = {"overture": "https://overturemaps-us-west-2.s3.amazonaws.com/?list-type=2&prefix=release/&delimiter=/",
         "osm": "https://overpass-api.de/api/status", "places": "https://places.googleapis.com/",
         "apollo": "https://api.apollo.io/", "reddit": "https://www.reddit.com/robots.txt"}


def _line(lead, cfg):
    contact = lead["email"] or lead["phone"] or lead["website"] or lead["url"] or ""
    return (f"  #{lead['id']:<4} {lead['score']:>3} {scoring.tier(lead['score'], cfg):<4} "
            f"{outreach.channel_for(lead):<9} {(lead['segment'] or '-'):<18} "
            f"{display_name(lead)[:34]:<34} {contact}")


def _table(rows):
    if not rows:
        print("  (no leads yet)")
        return
    cols = list(rows[0])
    widths = [max(len(c), *(len(str(r[c])) for r in rows)) for c in cols]
    for r in [dict(zip(cols, cols))] + rows:
        print("  " + "  ".join(str(r[c]).ljust(w) for c, w in zip(cols, widths)))


def cmd_doctor(cfg):
    ok = True
    probs = config.validate(cfg)
    print("Config:", "OK" if not probs else "")
    for p in probs:
        print("  ! " + p)
    print("Sources:")
    for name in cfg["sources"]:
        if name not in REGISTRY:
            continue
        key = config.SOURCE_KEYS.get(name)
        if name == "overture":
            try:
                import pyarrow  # noqa: F401
            except ImportError:
                print("  ! overture: needs pyarrow - run: pip install pyarrow")
                ok = False
                continue
        if key and not os.environ.get(key):
            print(f"  ! {name}: {key} is not set")
            ok = False
            continue
        try:
            urllib.request.urlopen(urllib.request.Request(PROBE[name], headers={"User-Agent": "leadgen-doctor"}), timeout=10)
            print(f"  ok {name}: reachable")
        except urllib.error.HTTPError:
            print(f"  ok {name}: reachable")
        except OSError as ex:
            print(f"  ! {name}: cannot reach {PROBE[name].split('/')[2]} ({ex})")
            ok = False
    return 0 if ok and not probs else 1


def main(argv=None):
    p = argparse.ArgumentParser(prog="leadgen", description="Lead generation toolkit")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, help):
        s = sub.add_parser(name, help=help)
        s.add_argument("slug", help="business folder name under businesses/")
        return s

    add("new", "scaffold a new business from the example config")
    add("doctor", "check the config, API keys and network access")
    s = add("collect", "pull leads from the enabled sources, dedupe and score")
    s.add_argument("--source", choices=sorted(REGISTRY), help="only this source")
    s = add("categories", "list the business types Overture has in your area (to fill sources.overture.categories)")
    s.add_argument("text", nargs="?", default="", help="only types containing this text")
    s = add("import", "import a CSV of leads")
    s.add_argument("file")
    s.add_argument("--source", default="import", help="label for where the list came from")
    s = add("enrich", "find missing emails on leads' own websites")
    s.add_argument("--min-score", type=int, default=0)
    s.add_argument("--limit", type=int, default=50)
    add("score", "re-score every lead (after editing the config)")
    s = add("draft", "write first messages and due follow-ups")
    s.add_argument("--min-score", type=int, default=0)
    s.add_argument("--limit", type=int)
    s.add_argument("--force", action="store_true", help="rewrite existing drafts")
    add("next", "today's actions: who to contact, follow up with, or close out")
    s = add("show", "print one lead with its draft")
    s.add_argument("id", type=int)
    s = add("mark", "record an outcome for one or more leads")
    s.add_argument("status", choices=STATUSES)
    s.add_argument("ids", type=int, nargs="+")
    s = add("export", "write leads to businesses/<slug>/exports/leads.csv")
    s.add_argument("--min-score", type=int, default=0)
    s.add_argument("--limit", type=int)
    add("stats", "lead counts by source and status")
    s = add("report", "funnel by source or segment: what is actually working")
    s.add_argument("--by", choices=["source", "segment"], default="source")
    s = add("purge", "delete all leads from one source (e.g. the sample data)")
    s.add_argument("--source", required=True)
    s = add("app", "open the interactive Lead Finder on this computer")
    s.add_argument("--port", type=int, default=8000)
    s = add("serve", "run the landing page, lead form and dashboard")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    loc = sub.add_parser("locate", help="look up latitude/longitude for a place name")
    loc.add_argument("place")
    a = p.parse_args(argv)

    try:
        if a.cmd == "locate":
            from . import geo
            for hit in geo.locate(a.place):
                print(f"  {hit['lat']}, {hit['lon']}   {hit['name']}")
            return 0
        if a.cmd == "new":
            d = config.business_dir(a.slug)
            if os.path.exists(d):
                sys.exit(f"{d} already exists")
            os.makedirs(d)
            shutil.copy(TEMPLATE, os.path.join(d, "business.json"))
            print(f"Created {d}/business.json - edit it, then: python -m leadgen doctor {a.slug}")
            return 0
        cfg = config.load(a.slug)
        if a.cmd == "doctor":
            return cmd_doctor(cfg)
        if a.cmd == "categories":
            from .sources import overture
            sc = cfg["sources"].get("overture")
            if not sc:
                sys.exit('Add "overture": {"around": [lat, lon, radius_m]} to sources in business.json first.')
            _table([{"category": c, "places": n, "with_email": m} for c, n, m in overture.categories(sc, a.text)[:200]])
            return 0
        if a.cmd == "app":
            from .server import serve
            token = os.environ.get("LEADGEN_ADMIN_TOKEN", "")
            serve(a.slug, "127.0.0.1", a.port, open_app=not token, home="app")
            return 0
        store = Store(config.db_path(a.slug))
        if a.cmd == "collect":
            for name, mod in REGISTRY.items():
                sc = cfg["sources"].get(name)
                if sc is None or (a.source and a.source != name):
                    continue
                new = total = 0
                try:
                    for lead in mod.collect(cfg, sc):
                        total += 1
                        new += store.add(name, lead)
                except (config.ConfigError, OSError, ValueError, KeyError) as ex:
                    print(f"[{name}] stopped after {total} leads: {ex}")
                print(f"[{name}] {total} found, {new} new")
            scoring.score_all(store, cfg)
        elif a.cmd == "import":
            new = total = 0
            for lead in csvio.read_rows(a.file):
                total += 1
                new += store.add(a.source, lead)
            scoring.score_all(store, cfg)
            print(f"{total} rows, {new} new, {total - new} duplicates or empty")
        elif a.cmd == "enrich":
            checked, found = enrich.enrich_all(store, a.min_score, a.limit)
            scoring.score_all(store, cfg)
            print(f"{checked} websites checked, {found} emails found")
        elif a.cmd == "score":
            print(f"{scoring.score_all(store, cfg)} scores updated")
        elif a.cmd == "draft":
            print(f"{outreach.draft_all(store, cfg, a.min_score, a.limit, a.force)} drafts written")
        elif a.cmd == "next":
            titles = {"reply_now": "REPLIED - answer these first", "inbound": "INBOUND - they asked, respond today",
                      "followups_due": "FOLLOW-UPS DUE", "first_touch": "FIRST CONTACT (best new leads)",
                      "close_out": "NO REPLY after all touches - mark lost"}
            for key, leads in outreach.agenda(store, cfg).items():
                if leads:
                    print(f"\n{titles[key]} ({len(leads)})")
                    for lead in leads:
                        print(_line(lead, cfg))
            print(f"\nSee a message: python -m leadgen show {a.slug} <id>   "
                  f"After sending: python -m leadgen mark {a.slug} contacted <id>")
        elif a.cmd == "show":
            lead = store.get(a.id)
            if not lead:
                sys.exit(f"no lead #{a.id}")
            for k in ("id", "source", "status", "score", "segment", "touches", "name", "company", "title",
                      "email", "phone", "website", "location", "url", "signal", "notes"):
                if lead[k] not in (None, ""):
                    print(f"{k:>9}: {lead[k]}")
            print(f"\n--- draft ({outreach.channel_for(lead)}) ---\n{lead['draft'] or '(none - run draft)'}")
        elif a.cmd == "mark":
            for i in a.ids:
                print(f"#{i}: {a.status}" if store.mark(i, a.status) else f"#{i}: not found")
        elif a.cmd == "export":
            out = os.path.join(config.business_dir(a.slug), "exports")
            os.makedirs(out, exist_ok=True)
            path = os.path.join(out, "leads.csv")
            leads = store.all(a.min_score, limit=a.limit)
            for lead in leads:
                lead.update(tier=scoring.tier(lead["score"], cfg), channel=outreach.channel_for(lead),
                            first_name=outreach.first_name(lead))
            csvio.write(path, leads)
            print(f"{len(leads)} leads -> {path}")
        elif a.cmd == "stats":
            print(json.dumps(store.stats(), indent=2))
        elif a.cmd == "report":
            _table(store.report(a.by))
        elif a.cmd == "purge":
            print(f"{store.purge(a.source)} leads deleted")
        elif a.cmd == "serve":
            from .server import serve
            serve(a.slug, a.host, a.port)
    except (config.ConfigError, OSError, ValueError) as ex:
        sys.exit(str(ex))
    return 0
