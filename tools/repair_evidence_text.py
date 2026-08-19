"""重新解析历史证据中的二进制附件，修复 extracted_text 乱码。

用法：python3 tools/repair_evidence_text.py --db aurora.db [--dry-run]

针对 parser_status 为 unknown_type/error 或 extracted_text 以 ZIP 魔数开头的证据，
从对象存储读取原始字节，用修复后的 document_parser 重新解析并回写；
若新解析出表格行，则幂等地补跑岗位提取。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aurora_monitor.db import Database
from aurora_monitor.document_parser import parse_document
from aurora_monitor.positions import extract_positions, upsert_positions


def main() -> int:
    parser = argparse.ArgumentParser(description="repair garbled evidence text")
    parser.add_argument("--db", default="aurora.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    database = Database(args.db)
    conn = database.connection
    rows = conn.execute(
        """SELECT id, notice_id, source_url, content_type, object_path, parser_status
           FROM evidence_version
           WHERE parser_status IN ('unknown_type', 'error') OR extracted_text LIKE 'PK%'"""
    ).fetchall()

    stats = {"scanned": len(rows), "repaired": 0, "still_unsupported": 0, "positions_added": 0}
    for row in rows:
        body = database.read_object(row["object_path"])
        parsed = parse_document(body, row["content_type"] or "", row["source_url"])
        if args.dry_run:
            print(f"{row['id'][:12]} {row['parser_status']} -> {parsed.parser_status} text_len={len(parsed.text)} url={row['source_url']}")
            continue
        with database.transaction() as handle:
            handle.execute(
                "UPDATE evidence_version SET extracted_text=?, parser_status=?, parser_warnings=? WHERE id=?",
                (parsed.text, parsed.parser_status, json.dumps(parsed.warnings, ensure_ascii=False), row["id"]),
            )
        if parsed.parser_status in {"parsed", "empty"}:
            stats["repaired"] += 1
        else:
            stats["still_unsupported"] += 1
        if parsed.rows:
            try:
                records = extract_positions(parsed, row["source_url"])
                if records:
                    upsert_positions(database, row["notice_id"], row["id"], records)
                    stats["positions_added"] += len(records)
            except Exception as exc:
                print(f"position extraction failed for {row['id'][:12]}: {exc}", file=sys.stderr)
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
