import argparse
import json
import os
import shutil
import sys

from . import config, csvio, outreach, scoring
from .db import Store
from .sources import REGISTRY

TEMPLATE = os.path.join(config.ROOT, "example", "business.json")


def main(argv=None):
    p = argparse.ArgumentParser(prog="leadgen", description="Lead generation toolkit")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("new", "collect", "import", "score", "draft", "export", "stats", "serve"):
        s = sub.add_parser(name)
        s.add_argument("slug")
        if name == "collect":
            s.add_argument("--source", choices=sorted(REGISTRY), help="only this source")
        if name == "import":
            s.add_argument("file")
            s.add_argument("--source", default="import")
        if name in ("draft", "export"):
            s.add_argument("--min-score", type=int, default=0)
            s.add_argument("--limit", type=int)
        if name == "draft":
            s.add_argument("--force", action="store_true")
        if name == "serve":
            s.add_argument("--host", default="127.0.0.1")
            s.add_argument("--port", type=int, default=8000)
    a = p.parse_args(argv)

    try:
        if a.cmd == "new":
            d = config.business_dir(a.slug)
            if os.path.exists(d):
                sys.exit(f"{d} already exists")
            os.makedirs(d)
            shutil.copy(TEMPLATE, os.path.join(d, "business.json"))
            print(f"Created {d}/business.json - edit it, then: python -m leadgen collect {a.slug}")
            return 0
        cfg = config.load(a.slug)
        store = Store(config.db_path(a.slug))
        if a.cmd == "collect":
            for name, mod in REGISTRY.items():
                sc = cfg["sources"].get(name)
                if not sc or (a.source and a.source != name):
                    continue
                new = total = 0
                try:
                    for lead in mod.collect(cfg, sc):
                        total += 1
                        new += store.add(name, lead)
                except (config.ConfigError, OSError) as ex:
                    print(f"[{name}] skipped: {ex}")
                    continue
                print(f"[{name}] {total} found, {new} new")
            scoring.score_all(store, cfg)
        elif a.cmd == "import":
            new = total = 0
            for lead in csvio.read_rows(a.file):
                total += 1
                new += store.add(a.source, lead)
            scoring.score_all(store, cfg)
            print(f"{total} rows, {new} new")
        elif a.cmd == "score":
            print(f"{scoring.score_all(store, cfg)} scores updated")
        elif a.cmd == "draft":
            print(f"{outreach.draft_all(store, cfg, a.min_score, a.limit, a.force)} drafts written")
        elif a.cmd == "export":
            out = os.path.join(config.business_dir(a.slug), "exports")
            os.makedirs(out, exist_ok=True)
            path = os.path.join(out, "leads.csv")
            leads = store.all(a.min_score, limit=a.limit)
            csvio.write(path, leads)
            print(f"{len(leads)} leads -> {path}")
        elif a.cmd == "stats":
            print(json.dumps(store.stats(), indent=2))
        elif a.cmd == "serve":
            from .server import serve
            serve(a.slug, a.host, a.port)
    except config.ConfigError as ex:
        sys.exit(str(ex))
    return 0
