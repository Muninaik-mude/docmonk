import os
import sys
import time
from urllib.parse import urlparse

import psycopg2


def _get_db_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


def _connect(db_url: str) -> None:
    parts = urlparse(db_url)
    if parts.scheme not in ("postgres", "postgresql"):
        raise RuntimeError(f"Unsupported DB scheme: {parts.scheme}")
    conn = psycopg2.connect(
        dbname=parts.path.lstrip("/"),
        user=parts.username,
        password=parts.password,
        host=parts.hostname,
        port=parts.port or 5432,
        connect_timeout=5,
        sslmode=os.getenv("PGSSLMODE", "prefer"),
    )
    conn.close()


def main() -> int:
    timeout_s = int(os.getenv("DB_WAIT_TIMEOUT", "60"))
    interval_s = 2
    db_url = _get_db_url()
    deadline = time.time() + timeout_s

    last_err = None
    while time.time() < deadline:
        try:
            _connect(db_url)
            print("Database is ready.")
            return 0
        except Exception as exc:
            last_err = exc
            print(f"Database not ready yet: {exc}")
            time.sleep(interval_s)

    print(f"Database not ready after {timeout_s}s: {last_err}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
