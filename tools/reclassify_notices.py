"""按最新关键词策略重新判定存量公告（如新增流程词表后批量降级流程性公告）。

用法：python3 tools/reclassify_notices.py --db aurora.db [--dry-run]

只更新 notice.decision 与 matched_terms；已解析的岗位、证据不受影响。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aurora_monitor.db import Database
from aurora_monitor.filtering import decide


def _loads(value):
    try:
        return json.loads(value) if value else []
    except (TypeError, json.JSONDecodeError):
        return []


def main() -> int:
    parser = argparse.ArgumentParser(description="reclassify notices with latest keyword policies")
    parser.add_argument("--db", default="aurora.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    database = Database(args.db)
    conn = database.connection
    policies = {}
    for row in conn.execute("SELECT id, version, include_any, exclude_any, workflow_terms, process_terms FROM keyword_policy"):
        policies[(row["id"], row["version"])] = row
    sources = {row["id"]: row for row in conn.execute("SELECT id, keyword_policy_id, keyword_policy_version FROM source")}

    transitions = Counter()
    updated = skipped = 0
    rows = conn.execute("SELECT id, source_id, title, decision FROM notice").fetchall()
    for row in rows:
        source = sources.get(row["source_id"])
        if not source:
            skipped += 1
            continue
        policy = policies.get((source["keyword_policy_id"], source["keyword_policy_version"]))
        if not policy:
            skipped += 1
            continue
        result = decide(row["title"], _loads(policy["include_any"]), _loads(policy["exclude_any"]), _loads(policy["workflow_terms"]), _loads(policy["process_terms"]))
        transitions[(row["decision"], result.decision)] += 1
        if result.decision == row["decision"]:
            continue
        updated += 1
        if not args.dry_run:
            conn.execute(
                "UPDATE notice SET decision=?, matched_terms=? WHERE id=?",
                (result.decision, json.dumps(result.matched_terms, ensure_ascii=False), row["id"]),
            )
    if not args.dry_run:
        conn.commit()
    print(json.dumps({"scanned": len(rows), "updated": updated, "skipped": skipped,
                      "transitions": {f"{old}->{new}": count for (old, new), count in sorted(transitions.items()) if old != new}},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
