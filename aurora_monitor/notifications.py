from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from .db import Database


@dataclass(frozen=True)
class DispatchStats:
    sent: int = 0
    failed: int = 0
    unsupported: int = 0


def _console_sender(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


class NotificationDispatcher:
    def __init__(self, database: Database, senders: dict[str, Callable[[dict], None]] | None = None) -> None:
        self.db = database
        self.senders = {"console": _console_sender, "in_app": _console_sender}
        if senders:
            self.senders.update(senders)

    def dispatch(self, limit: int = 100) -> DispatchStats:
        sent = failed = unsupported = 0
        for row in self.db.pending_notifications(limit):
            sender = self.senders.get(row["channel"])
            if sender is None:
                self.db.mark_notification_failed(row["id"], f"unsupported notification channel: {row['channel']}")
                unsupported += 1
                continue
            payload = {
                "notification_id": row["id"],
                "event_type": row["event_type"].split(":", 1)[0],
                "event_key": row["event_type"],
                "profile": row["profile_name"],
                "title": row["title"],
                "url": row["url"],
                "published_at": row["published_at"],
                "publisher": row["publisher"],
                "region_code": row["region_code"],
                "institution": row["institution_name"],
            }
            try:
                sender(payload)
            except Exception as exc:
                self.db.mark_notification_failed(row["id"], str(exc))
                failed += 1
            else:
                self.db.mark_notification_sent(row["id"])
                sent += 1
        return DispatchStats(sent, failed, unsupported)
