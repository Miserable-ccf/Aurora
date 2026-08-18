from __future__ import annotations

import os
from pathlib import Path


def load_local_env() -> None:
    """Load simple KEY=VALUE entries without overwriting real environment variables."""
    candidates = (Path.cwd() / ".env", Path(__file__).resolve().parents[1] / ".env")
    for path in candidates:
        if not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()
            if not key or key in os.environ:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            os.environ[key] = value
