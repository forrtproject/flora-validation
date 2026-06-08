"""
Export the validated table to data/validated_export.csv.

Columns match exactly what the FLoRA Preparation Pipeline R script expects:
  doi_r, doi_o, url_r, url_o, ref_r, ref_o, abstract_r,
  year_r, year_o, type, outcome, outcome_quote,
  outcome_quote_source, source

Usage:
  python export_validated.py

Reads DATABASE_URL from .env (or environment).
Output: data/validated_export.csv
"""

import os
import sys
from pathlib import Path

import pandas as pd
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    sys.exit("ERROR: DATABASE_URL is not set. Add it to your .env file.")

OUTPUT_PATH = Path(__file__).parent / "data" / "validated_export.csv"
OUTPUT_PATH.parent.mkdir(exist_ok=True)

QUERY = """
    SELECT
        v.doi_r,
        v.doi_o,
        v.url_r,
        v.url_o,
        v.ref_r,
        v.ref_o,
        v.abstract_r,
        v.year_r,
        v.year_o,
        v.type,
        v.outcome,
        v.outcome_quote,
        v.out_quote_source   AS outcome_quote_source,
        COALESCE(m.source, 'validated_db') AS source
    FROM  validated v
    LEFT JOIN record_metadata m ON m.record_id = v.record_id
    ORDER BY v.validated_at;
"""

print("Connecting to database...")
try:
    conn = psycopg2.connect(DATABASE_URL)
    df   = pd.read_sql(QUERY, conn)
    conn.close()
except Exception as e:
    sys.exit(f"ERROR: Could not connect or query: {e}")

print(f"  Rows fetched : {len(df)}")
print(f"  Columns      : {list(df.columns)}")

df.to_csv(OUTPUT_PATH, index=False)
print(f"  Saved to     : {OUTPUT_PATH}")
print("Done.")
