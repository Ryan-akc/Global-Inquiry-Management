"""One-time, guarded migration of the app's SQLite records to Supabase Postgres."""

import argparse
import os
import re
import sqlite3
import sys


TABLES = ("companies", "activities", "users")


def main():
    parser = argparse.ArgumentParser(
        description="Copy companies, activity history, and login accounts into an initialized Supabase database."
    )
    parser.add_argument("sqlite_file", help="Path to the existing inquiry_manager.db file")
    args = parser.parse_args()

    database_url = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not database_url:
        raise SystemExit("Set SUPABASE_DB_URL in this terminal session before running the migration.")
    if not os.path.isfile(args.sqlite_file):
        raise SystemExit(f"SQLite file not found: {args.sqlite_file}")

    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit("Install dependencies first: py -m pip install -r requirements.txt") from exc

    source = sqlite3.connect(args.sqlite_file)
    source.row_factory = sqlite3.Row
    target = psycopg.connect(
        database_url,
        sslmode="require",
        connect_timeout=15,
        prepare_threshold=None,
    )
    copied = {}
    try:
        with target.transaction():
            existing_companies = target.execute("SELECT COUNT(*) FROM public.companies").fetchone()[0]
            existing_activities = target.execute("SELECT COUNT(*) FROM public.activities").fetchone()[0]
            if existing_companies or existing_activities:
                raise RuntimeError(
                    "Migration stopped: the Supabase database already contains company or activity data. "
                    "Use a new/empty project or make a backup and choose a deliberate merge plan."
                )

            for table in TABLES:
                source_tables = {
                    row["name"] for row in source.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                if table not in source_tables:
                    copied[table] = 0
                    continue

                source_columns = [row["name"] for row in source.execute(f"PRAGMA table_info({table})")]
                target_columns = {
                    row[0] for row in target.execute(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema='public' AND table_name=%s", (table,)
                    ).fetchall()
                }
                missing_columns = set(source_columns) - target_columns
                if missing_columns:
                    raise RuntimeError(
                        f"Supabase table {table} is missing source columns: {', '.join(sorted(missing_columns))}. "
                        "Start the current app against Supabase first so its schema can initialize, then retry."
                    )

                records = source.execute(f"SELECT * FROM {table}").fetchall()
                if table == "users":
                    usernames = [str(record["username"]).casefold() for record in records]
                    if len(usernames) != len(set(usernames)):
                        raise RuntimeError("The local user list contains IDs that differ only by letter case; resolve these before migration.")

                safe_columns = [column for column in source_columns if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", column)]
                if len(safe_columns) != len(source_columns):
                    raise RuntimeError(f"Unexpected column name found in {table}; migration stopped for safety.")
                columns_sql = ",".join(f'"{column}"' for column in safe_columns)
                placeholders = ",".join(["%s"] * len(safe_columns))
                insert_sql = f"INSERT INTO public.{table} ({columns_sql}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
                values = []
                for record in records:
                    value = [record[column] for column in safe_columns]
                    if table == "users":
                        username_index = safe_columns.index("username")
                        value[username_index] = str(value[username_index]).casefold()
                    values.append(value)
                before_count = target.execute(f"SELECT COUNT(*) FROM public.{table}").fetchone()[0]
                if values:
                    target.cursor().executemany(insert_sql, values)
                after_count = target.execute(f"SELECT COUNT(*) FROM public.{table}").fetchone()[0]
                copied[table] = after_count - before_count

            for table, id_column in (("companies", "company_id"), ("activities", "activity_id")):
                sequence = target.execute(
                    "SELECT pg_get_serial_sequence(%s, %s)", (f"public.{table}", id_column)
                ).fetchone()[0]
                max_id = target.execute(f"SELECT MAX({id_column}) FROM public.{table}").fetchone()[0]
                if sequence and max_id is not None:
                    target.execute("SELECT setval(%s, %s, true)", (sequence, max_id))

        for table in TABLES:
            print(f"{table}: {copied.get(table, 0)} rows copied")
        print("Migration completed. Keep the original SQLite database as a backup until the hosted app is confirmed.")
    finally:
        source.close()
        target.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
