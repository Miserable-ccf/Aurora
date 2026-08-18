from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .db import Database
from .monitor import Monitor
from .notifications import NotificationDispatcher


def main() -> int:
    parser = argparse.ArgumentParser(description="Aurora lightweight notice monitor")
    parser.add_argument("--db", default="aurora.db")
    parser.add_argument("command", choices=["init", "run-once", "run-cycle", "watch", "dispatch-notifications", "list-notices", "list-candidates", "source-health", "validate-institutions", "import-policy", "import-sources", "import-source-yaml", "import-profile", "import-institutions"])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--profile-id")
    parser.add_argument("--file")
    parser.add_argument("--batch-id", default="manual")
    parser.add_argument("--provider", default="user")
    parser.add_argument("--dispatch", action="store_true", help="dispatch pending notifications after each monitoring cycle")
    args = parser.parse_args()
    db = Database(Path(args.db))
    try:
        db.init_schema()
        db.seed_regions()
        if args.command == "run-once":
            print(json.dumps(Monitor(db).run_once(args.limit), ensure_ascii=False))
        elif args.command == "run-cycle":
            monitor_stats = Monitor(db).run_once(args.limit)
            dispatch_stats = NotificationDispatcher(db).dispatch(args.limit or 100)
            print(json.dumps({"monitor": monitor_stats, "dispatch": dispatch_stats.__dict__}, ensure_ascii=False))
        elif args.command == "watch":
            dispatcher = NotificationDispatcher(db)

            def after_cycle(stats):
                output = {"monitor": stats}
                if args.dispatch:
                    output["dispatch"] = dispatcher.dispatch(args.limit or 100).__dict__
                print(json.dumps(output, ensure_ascii=False), flush=True)

            Monitor(db).run_forever(args.interval, args.limit, after_cycle)
        elif args.command == "list-notices":
            if not args.profile_id:
                parser.error("list-notices requires --profile-id")
            rows = [dict(row) for row in db.list_profile_notices(args.profile_id, args.limit or 50)]
            print(json.dumps(rows, ensure_ascii=False))
        elif args.command == "list-candidates":
            rows = [dict(row) for row in db.list_notices("candidate", args.limit or 50)]
            print(json.dumps(rows, ensure_ascii=False))
        elif args.command == "source-health":
            rows = [dict(row) for row in db.list_source_health(args.limit or 100)]
            print(json.dumps(rows, ensure_ascii=False))
        elif args.command == "validate-institutions":
            if not args.file:
                parser.error("validate-institutions requires --file")
            print(json.dumps(db.validate_institutions_csv(args.file), ensure_ascii=False))
        elif args.command == "dispatch-notifications":
            stats = NotificationDispatcher(db).dispatch(args.limit or 100)
            print(json.dumps(stats.__dict__, ensure_ascii=False))
        elif args.command == "import-policy":
            if not args.file:
                parser.error("import-policy requires --file")
            print(json.dumps({"imported": db.add_policy_json(args.file)}, ensure_ascii=False))
        elif args.command == "import-sources":
            if not args.file:
                parser.error("import-sources requires --file")
            print(json.dumps({"imported": db.import_sources_json(args.file)}, ensure_ascii=False))
        elif args.command == "import-source-yaml":
            if not args.file:
                parser.error("import-source-yaml requires --file")
            print(json.dumps({"imported": db.import_sources_yaml(args.file)}, ensure_ascii=False))
        elif args.command == "import-profile":
            if not args.file:
                parser.error("import-profile requires --file")
            profile = json.loads(Path(args.file).read_text(encoding="utf-8"))
            db.add_profile(profile)
            print(json.dumps({"imported": 1}, ensure_ascii=False))
        elif args.command == "import-institutions":
            if not args.file:
                parser.error("import-institutions requires --file")
            print(json.dumps(db.import_institutions_csv(args.file, args.batch_id, args.provider), ensure_ascii=False))
    except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
