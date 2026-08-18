from __future__ import annotations

import argparse
import os


def main() -> int:
    parser = argparse.ArgumentParser(description="Aurora local recruitment information Web app")
    parser.add_argument("--db", default="aurora.db")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18100)
    args = parser.parse_args()
    os.environ["AURORA_DB_PATH"] = args.db
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("Web dependencies are missing. Run: python3 -m pip install -e '.[web]'") from exc
    uvicorn.run("aurora_web.main:app", host=args.host, port=args.port, reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
