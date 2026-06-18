"""Apply database/schema.sql to the database pointed to by $DATABASE_URL.

Idempotent — safe to run multiple times. Only touches the `ezjudgements` schema.

Usage:
    python -m database.init_db
"""

import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv()

SCHEMA_FILE = Path(__file__).parent / "schema.sql"


def main() -> None:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        sys.exit("DATABASE_URL not set. Put it in .env or export it.")

    sql = SCHEMA_FILE.read_text(encoding="utf-8")

    host = database_url.split("@")[-1].split("/")[0]
    print(f"Applying ezjudgements schema to {host} ...")

    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()

    print("Done. Tables:")
    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT tablename
                  FROM pg_tables
                 WHERE schemaname = 'ezjudgements'
                 ORDER BY tablename
                """
            )
            for (name,) in cur.fetchall():
                print(f"  - ezjudgements.{name}")


if __name__ == "__main__":
    main()
