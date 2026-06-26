"""
Export validated entries to two files:

1. data/validated_export.csv   — full validated dataset for the FLoRA pipeline
2. data/needs_manual_refs.csv  — entries whose identifiers can't be resolved by
                                  CrossRef/DataCite and will be dropped by the
                                  pipeline's title filter unless added to
                                  manual_references.xlsx

Columns in needs_manual_refs.csv:
  side          — which side needs fixing: "r" (replication), "o" (original), or "both"
  doi_r / url_r — replication identifier
  doi_o / url_o — original identifier
  ref_r / ref_o — short reference strings already in the DB (for context)
  title_r       — (blank) fill in to add to manual_references.xlsx
  author_r      — (blank) fill in to add to manual_references.xlsx
  year_r        — (blank) fill in to add to manual_references.xlsx
  journal_r     — (blank) fill in to add to manual_references.xlsx

Usage:
  python export_validated.py

Reads DATABASE_URL from .env (or environment).
"""

import os
import re
import sys
from pathlib import Path

import pandas as pd
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    sys.exit("ERROR: DATABASE_URL is not set. Add it to your .env file.")

EXPORT_PATH        = Path(__file__).parent / "data" / "validated_export.csv"
NEEDS_MANUAL_PATH  = Path(__file__).parent / "data" / "needs_manual_refs.csv"
EXPORT_PATH.parent.mkdir(exist_ok=True)

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
    LEFT JOIN LATERAL (
        SELECT source FROM record_metadata
        WHERE record_id = v.record_id
        LIMIT 1
    ) m ON true
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
print(f"  Unique doi_r : {df['doi_r'].nunique()}")
dup_doi_r = df[df.duplicated('doi_r', keep=False)].groupby('doi_r').size()
if len(dup_doi_r):
    print(f"  doi_r with multiple doi_o (legitimate multi-original replications): {len(dup_doi_r)}")

# ── 1. Write main export ──────────────────────────────────────────────────────
df.to_csv(EXPORT_PATH, index=False)
print(f"  Saved export : {EXPORT_PATH}")


# ── 2. Identify entries that need manual references ───────────────────────────

def is_real_doi(value: str | None) -> bool:
    """Return True if value is a DOI starting with '10.' (after basic cleaning)."""
    if not value or pd.isna(value):
        return False
    v = str(value).strip().lower()
    v = re.sub(r"^https?://(dx\.)?doi\.org/", "", v)
    v = re.sub(r"^doi:\s*", "", v)
    v = re.sub(r"\s.*$", "", v)   # drop anything after first whitespace
    return v.startswith("10.")


r_unresolvable = ~df["doi_r"].apply(is_real_doi) & df["url_r"].isna().where(df["url_r"] != "", other=True)
o_unresolvable = ~df["doi_o"].apply(is_real_doi) & df["url_o"].isna().where(df["url_o"] != "", other=True)

# Treat non-doi.org URLs in doi_r/doi_o as unresolvable too
def in_doi_r_is_url(val):
    """doi_r field contains a non-DOI URL (e.g. https://aisel.aisnet.org/...)."""
    if not val or pd.isna(val):
        return False
    v = str(val).strip().lower()
    return v.startswith("http") and "doi.org" not in v

r_url_as_doi = df["doi_r"].apply(in_doi_r_is_url)
o_url_as_doi = df["doi_o"].apply(in_doi_r_is_url)

needs_r = r_unresolvable | r_url_as_doi
needs_o = o_unresolvable | o_url_as_doi
needs_any = needs_r | needs_o

if needs_any.sum() == 0:
    print("  All entries have resolvable DOIs — no manual references needed.")
    NEEDS_MANUAL_PATH.write_text(
        "side,doi_r,url_r,doi_o,url_o,ref_r,ref_o,title_r,author_r,year_r,journal_r\n"
    )
    print(f"  Saved (empty): {NEEDS_MANUAL_PATH}")
else:
    flagged = df[needs_any].copy()
    flagged["side"] = "both"
    flagged.loc[needs_r & ~needs_o, "side"] = "r"
    flagged.loc[needs_o & ~needs_r, "side"] = "o"

    # Blank columns for the person filling in manual_references.xlsx
    for col in ("title_r", "author_r", "year_r", "journal_r"):
        flagged[col] = ""

    output_cols = [
        "side",
        "doi_r", "url_r", "doi_o", "url_o",
        "ref_r", "ref_o",
        "title_r", "author_r", "year_r", "journal_r",
    ]
    flagged[output_cols].to_csv(NEEDS_MANUAL_PATH, index=False)

    print(f"\n  ⚠  {needs_any.sum()} entries need manual references:")
    print(f"       replication side (r) : {needs_r.sum()}")
    print(f"       original side (o)    : {needs_o.sum()}")
    print(f"       both sides           : {(needs_r & needs_o).sum()}")
    print(f"  Saved: {NEEDS_MANUAL_PATH}")
    print("  → Fill in title_r/author_r/year_r/journal_r and add to manual_references.xlsx")

print("Done.")
