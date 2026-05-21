"""
db_reset.py — wipe all data except validators.
Run: DATABASE_URL=<url> python db_reset.py
"""
import os, psycopg2

TABLES_TO_CLEAR = [
    "validated",
    "validation_queue",
    "record_metadata",
    "unvalidated",
    "validators",
]

def main():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("Set DATABASE_URL before running this script.")

    print("Tables to be cleared:")
    for t in TABLES_TO_CLEAR:
        print(f"  - {t}")
    confirm = input("\nType YES to proceed: ").strip()
    if confirm != "YES":
        print("Aborted.")
        return

    conn = psycopg2.connect(url)
    cur = conn.cursor()
    for table in TABLES_TO_CLEAR:
        cur.execute(f"TRUNCATE TABLE {table} CASCADE")
        print(f"  cleared {table}")
    conn.commit()
    cur.close()
    conn.close()
    print("\nDone.")

if __name__ == "__main__":
    main()
